# Muggle Word Cards — 后端 API（FastAPI + SQLite）
# -*- coding: utf-8 -*-
"""接口一览：
- GET  /health                  健康检查（公开）
- POST /api/auth/register       注册账号 -> {token, user}
- POST /api/auth/login          登录 -> {token, user}
- GET  /api/auth/me            当前登录用户（需 Bearer token）
- POST /api/extract             上传 1-3 张图片 -> OCR -> DeepSeek 提取 -> 返回草稿（不保存）
- POST /api/import              把确认过的草稿存成新分组（自动跳过已在库里的词）
- GET  /api/groups              分组列表（含每组词数）
- GET  /api/groups/{id}/words   某分组下的全部单词
- POST /api/review/{word_id}    记录一次复习结果（认识/不认识），按阶梯排期
- GET  /api/review/due          今天到期的词
- GET  /api/wanted               通缉名单（连续答错 >= 2 次的词）

鉴权：除 /health 外全部 /api/* 接口都要求请求头带
Authorization: Bearer <token>，否则 401；数据按登录用户 id 隔离，
查别人的分组/单词一律返回 404（不泄露存在性）。

复习规则（和主版本完全一致）：
- 阶梯 LADDER = [1, 2, 4, 7, 15, 30] 天
- 日常复习：答对 -> 阶梯进一位，下次到期 = 今天 + 阶梯天数，连错清零；
  答错 -> 连错 +1，阶梯归零，下次到期 = 明天。
- 三遍筛选（新词入库当晚）：第 1 遍就认识 -> +4 天；第 2/3 遍救回来 -> +2 天；
  三遍都失败 -> 明天再来。调用时传 pass_number=1/2/3 启用这套规则，不传就是日常复习。
- 答对一定清零连错；连错 >= 2 的词进通缉名单。
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import auth, db
from .llm import DeepSeekClient, LLMError
from .ocr import OCRError, ocr_image
from .ratelimit import DailyRateLimiter, get_client_ip

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
_db_env = os.getenv("MWC_DB_PATH", "data/muggle.db")
_db_path = Path(_db_env)
DB_PATH = _db_path if _db_path.is_absolute() else BASE_DIR / _db_path

# 复习阶梯（天数）。stage 是下标：新词 stage=0，答对一次进一位。
LADDER = [1, 2, 4, 7, 15, 30]
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 单张图片 10MB 上限
DEFAULT_USER_ID = 1  # 启动时保留的默认本地用户（单人使用时代的数据）。
# 接口不再用它：全部 /api/* 数据接口都按登录用户的 id 隔离，见 get_current_user。

limiter = DailyRateLimiter(int(os.getenv("EXTRACT_DAILY_LIMIT", "20")))

app = FastAPI(title="Muggle Word Cards API", version="0.1.0")

# 本地开发先放开跨域，方便前端（localhost:3000/5173）直接调。
# 上线后改成只允许自己的域名，例如 allow_origins=["https://mugglewords.app"]。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    db.init_db(DB_PATH)
    db.ensure_user(DB_PATH, DEFAULT_USER_ID, "local")


# ---------- 数据模型 ----------

class WordDraft(BaseModel):
    """一张待入库的卡（/api/extract 返回的格式，/api/import 接收的格式）。"""

    word: str
    phonetic: str = ""
    meaning: str
    phrase: str = ""
    hook: str = ""
    trap: str = ""


class ImportRequest(BaseModel):
    group_label: str = Field(..., min_length=1, description="分组名，如 2023-12 第1套 P1 文章词")
    words: list[WordDraft] = Field(..., min_length=1)
    source: str | None = None


class ReviewRequest(BaseModel):
    known: bool = Field(..., description="这次复习认识这个词吗")
    pass_number: int | None = Field(
        default=None,
        description="三遍筛选时传 1/2/3；日常复习不传",
    )


class RegisterRequest(BaseModel):
    email: str = Field(..., description="邮箱，登录用")
    password: str = Field(..., description="密码，至少 6 位")
    name: str | None = Field(default=None, description="昵称，不填默认用邮箱前缀")


class LoginRequest(BaseModel):
    email: str
    password: str


# ---------- 鉴权 ----------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 6

bearer_scheme = HTTPBearer(auto_error=False)


def _public_user(user: dict) -> dict:
    """返回给前端的用户信息：绝不带 password_hash。"""
    return {"id": user["id"], "email": user.get("email"), "name": user["name"]}


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """依赖：解析 Authorization: Bearer <token>，返回登录用户（users 表整行）。

    没带 token、token 无效/过期、用户不存在，一律 401。
    """
    if credentials is None:
        raise HTTPException(401, "需要登录：请在请求头带 Authorization: Bearer <token>")
    try:
        user_id = auth.decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "token 无效或已过期，请重新登录")
    user = db.get_user_by_id(DB_PATH, user_id)
    if user is None:
        raise HTTPException(401, "用户不存在，请重新登录")
    return user


@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterRequest) -> dict:
    email = payload.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "邮箱格式不正确")
    if len(payload.password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"密码至少 {MIN_PASSWORD_LEN} 位")
    name = (payload.name or "").strip() or email.split("@")[0]
    try:
        user_id = db.create_user(DB_PATH, email, name, auth.hash_password(payload.password))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "该邮箱已注册，请直接登录")
    user = db.get_user_by_id(DB_PATH, user_id)
    return {"token": auth.create_token(user_id), "user": _public_user(user)}


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict:
    email = payload.email.strip().lower()
    user = db.get_user_by_email(DB_PATH, email)
    # 邮箱不存在、没设密码（老默认用户）、密码不对，都返回同一个 401，
    # 不告诉调用方到底是哪一步错了。
    if (
        user is None
        or not user.get("password_hash")
        or not auth.verify_password(payload.password, user["password_hash"])
    ):
        raise HTTPException(401, "邮箱或密码错误")
    return {"token": auth.create_token(user["id"]), "user": _public_user(user)}


@app.get("/api/auth/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    return _public_user(user)


# ---------- 健康检查 ----------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "db": str(DB_PATH)}


# ---------- 提取：拍照 -> OCR -> 大模型（只出草稿，不保存） ----------

@app.post("/api/extract")
async def extract(
    request: Request,
    user: dict = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    exam: str = Form("cet6"),
) -> dict:
    if not 1 <= len(files) <= MAX_IMAGES:
        raise HTTPException(400, f"一次请上传 1-{MAX_IMAGES} 张图片")

    # 限流先行：没过限流连 OCR 都不跑，省钱。
    ip = get_client_ip(request)
    if not limiter.allow(ip):
        raise HTTPException(
            429,
            f"今日提取次数已用完（每 IP 每天 {limiter.limit} 次），明天再来。",
        )

    texts: list[str] = []
    for f in files:
        if not (f.content_type or "").startswith("image/"):
            raise HTTPException(400, f"文件 {f.filename!r} 不是图片")
        data = await f.read()
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, f"图片 {f.filename!r} 超过 10MB")
        try:
            texts.append(ocr_image(data))
        except OCRError as exc:
            raise HTTPException(502, str(exc))

    passage = "\n\n".join(t.strip() for t in texts if t.strip())
    if len(passage) < 20:
        raise HTTPException(422, "OCR 识别到的文字太少，请拍得更清晰一点再试")

    try:
        client = DeepSeekClient()
        draft = await client.extract_words(passage, exam=exam)
    except RuntimeError as exc:  # 没配 Key
        raise HTTPException(503, str(exc))
    except LLMError as exc:
        raise HTTPException(502, str(exc))

    return {
        "group_label": draft["group_label"],
        "words": draft["words"],
        "exam": (exam or "cet6").strip().lower() or "cet6",
        "passage_chars": len(passage),
        "remaining_today": limiter.remaining(ip),
    }


# ---------- 入库：确认过的草稿存成新分组 ----------

@app.post("/api/import")
def import_words(payload: ImportRequest, user: dict = Depends(get_current_user)) -> dict:
    user_id = user["id"]
    group_id = db.create_group(DB_PATH, user_id, payload.group_label, payload.source)
    imported: list[str] = []
    skipped: list[str] = []
    seen_in_batch: set[str] = set()
    for w in payload.words:
        key = w.word.strip().lower()
        if not key:
            continue
        if key in seen_in_batch or db.word_exists(DB_PATH, user_id, w.word):
            skipped.append(w.word)  # 已在库里的词跳过，不动它原来的进度
            continue
        seen_in_batch.add(key)
        db.insert_word(
            DB_PATH,
            group_id=group_id,
            user_id=user_id,
            word=w.word,
            phonetic=w.phonetic,
            meaning=w.meaning,
            phrase=w.phrase,
            hook=w.hook,
            trap=w.trap,
        )
        imported.append(w.word)
    return {"group_id": group_id, "imported": len(imported), "skipped": skipped}


# ---------- 分组与单词 ----------

@app.get("/api/groups")
def list_groups(user: dict = Depends(get_current_user)) -> list[dict]:
    return db.list_groups(DB_PATH, user["id"])


@app.get("/api/groups/{group_id}/words")
def list_group_words(group_id: int, user: dict = Depends(get_current_user)) -> list[dict]:
    if not db.group_exists(DB_PATH, group_id, user["id"]):
        raise HTTPException(404, f"分组 {group_id} 不存在")
    return db.list_words_in_group(DB_PATH, group_id, user["id"])


# ---------- 复习 ----------

def _apply_review_rules(word: dict, known: bool, pass_number: int | None, today: date) -> dict:
    """按规则算出复习后的 stage / due_on / fail_streak。纯函数，方便单测。"""
    stage = word["stage"]
    fail_streak = word["fail_streak"]
    if known:
        fail_streak = 0  # 答对一定清零连错
        if pass_number == 1:
            # 三遍筛选：第 1 遍就认识 -> +4 天
            stage, due = 2, today + timedelta(days=4)
        elif pass_number in (2, 3):
            # 三遍筛选：第 2/3 遍救回来 -> +2 天
            stage, due = 1, today + timedelta(days=2)
        else:
            # 日常复习：阶梯进一位
            stage = min(stage + 1, len(LADDER) - 1)
            due = today + timedelta(days=LADDER[stage])
    else:
        # 答错：连错 +1，阶梯归零，明天再来（筛选三遍都失败也是这条路）
        fail_streak += 1
        stage = 0
        due = today + timedelta(days=1)
    return {"stage": stage, "due_on": due.isoformat(), "fail_streak": fail_streak}


@app.post("/api/review/{word_id}")
def review_word(word_id: int, payload: ReviewRequest, user: dict = Depends(get_current_user)) -> dict:
    word = db.get_word(DB_PATH, word_id)
    # 别人的单词按不存在处理（404），不泄露存在性。
    if word is None or word["user_id"] != user["id"]:
        raise HTTPException(404, f"单词 {word_id} 不存在")
    if payload.pass_number is not None and payload.pass_number not in (1, 2, 3):
        raise HTTPException(400, "pass_number 只能是 1、2、3（或不传）")

    today = date.today()
    result = _apply_review_rules(word, payload.known, payload.pass_number, today)
    db.update_word_review(DB_PATH, word_id, **result)
    db.add_review(DB_PATH, word_id, known=payload.known, pass_number=payload.pass_number)
    return {
        **db.get_word(DB_PATH, word_id),
        "wanted": result["fail_streak"] >= 2,  # 刚进通缉名单的话，前端可以直接标出来
    }


@app.get("/api/review/due")
def due_today(user: dict = Depends(get_current_user)) -> list[dict]:
    return db.due_words(DB_PATH, user["id"])


@app.get("/api/wanted")
def wanted(user: dict = Depends(get_current_user)) -> list[dict]:
    return db.wanted_words(DB_PATH, user["id"])
