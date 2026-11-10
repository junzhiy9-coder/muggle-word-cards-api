# Muggle Word Cards — OCR 模块
# -*- coding: utf-8 -*-
# 把照片转成文字。默认用本地免费的 Tesseract，不需要联网、不花钱。
# 以后想换云端 OCR（识别率更高，尤其手写/模糊照片），直接实现下面的
# ocr_with_cloud() 即可，主流程（main.py）不用改。
from __future__ import annotations

import io


class OCRError(Exception):
    """OCR 失败时抛这个错，main.py 会把它转成 502 返回给前端。"""


def ocr_image(image_bytes: bytes, lang: str = "eng") -> str:
    """用本地 Tesseract 识别一张图片里的英文文字。

    依赖：pip 装 pytesseract + Pillow，以及系统里装 tesseract 可执行文件。
    macOS: brew install tesseract；Ubuntu/Debian: sudo apt install tesseract-ocr。
    没装的话会抛 OCRError，错误信息里会告诉用户怎么装。
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise OCRError(
            "OCR 依赖未安装：请执行 pip install pytesseract Pillow，"
            "并确认系统里装了 tesseract（brew install tesseract / apt install tesseract-ocr）。"
        )

    try:
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise OCRError(f"图片无法打开：{exc}")

    # 手机照片动辄几千万像素，免费版小服务器上 Tesseract 跑大图特别慢。
    # 先在服务端也压一下：最长边超过 2000 像素就等比缩小，并转灰度，
    # 英文印刷体识别几乎不受影响，速度能快好几倍。
    try:
        if max(image.size) > 2000:
            ratio = 2000 / max(image.size)
            image = image.resize(
                (int(image.width * ratio), int(image.height * ratio))
            )
        if image.mode != "L":
            image = image.convert("L")
    except Exception as exc:
        raise OCRError(f"图片预处理失败：{exc}")

    try:
        return pytesseract.image_to_string(image, lang=lang)
    except pytesseract.TesseractNotFoundError:
        raise OCRError(
            "找不到 tesseract 可执行文件：请安装 Tesseract OCR"
            "（brew install tesseract / apt install tesseract-ocr），"
            "或把 OCR_PROVIDER 改成云端方案（见 ocr_with_cloud）。"
        )


def ocr_with_cloud(image_bytes: bytes, provider: str = "") -> str:
    """【预留桩】云端 OCR 接入点，以后在这里接第三方 OCR 服务。

    怎么接（以腾讯云 / 阿里云 OCR 为例）：
    1. 在对应平台开通 OCR 服务，拿到 SecretId/SecretKey（放 .env，不进代码）；
    2. 在这里用官方 SDK 把 image_bytes 发过去，返回识别出的文字；
    3. 在 main.py 里根据 OCR_PROVIDER 环境变量选择调 ocr_image 还是这个函数。

    现在是桩，直接抛错，防止误以为已实现。
    """
    raise NotImplementedError(
        f"云端 OCR（provider={provider!r}）尚未接入。"
        "请在 app/ocr.py 的 ocr_with_cloud() 里按注释实现，或继续用本地 Tesseract。"
    )

# Muggle Word Cards — 后端
# -*- coding: utf-8 -*-
"""提取生词用的 system prompt。

这份 prompt 是产品的护城河：提取质量好不好，全看它写得细不细。
网站上线后如果发现提得不准，先改这里，再测，不要动代码逻辑。

拍照提取按用户定的考试来筛词：新用户在进入页填的考试（四级/六级/考研/雅思/托福）
会跟着提取请求一起传过来，`get_extraction_prompt(exam)` 按考试换对应的级别标准。
"""
from __future__ import annotations

# 各考试的级别标准。key 是接口收到的 exam 参数（小写）；未知值一律按 cet6 处理。
EXAM_PROFILES = {
    "cet4": {
        "exam_name": "大学英语四级（CET-4）",
        "level_rule": (
            "只提 CET-4 级别的词：跳过初中、高中就已经掌握的常见词"
            "（如 people、important、because、however），也跳过人名、地名、机构名等专有名词。"
            "已经达到六级水平的词也可以提，但优先保证四级核心词。"
        ),
        "group_hint": (
            "格式统一为「真题年月 第几套 哪篇」，例如「2024-06 第1套 P1 文章词」。"
            "如果无法从文本判断是哪套真题，就用文章主题起一个简短的名字，如「人工智能与艺术」。"
        ),
    },
    "cet6": {
        "exam_name": "大学英语六级（CET-6）",
        "level_rule": (
            "只提 CET-6 级别的词：跳过初中、高中就已经掌握的常见词"
            "（如 people、important、because、however），"
            "也跳过人名、地名、机构名等专有名词。"
        ),
        "group_hint": (
            "格式统一为「真题年月 第几套 哪篇」，"
            "例如「2023-12 第1套 P1 文章词」「2026-06 第3套 Section B · 儿童金钱」。"
            "如果无法从文本判断是哪套真题，就用文章主题起一个简短的名字，如「人工智能与艺术」。"
        ),
    },
    "kaoyan": {
        "exam_name": "考研英语",
        "level_rule": (
            "只提考研英语级别的词：跳过初中、高中、四六级已经掌握的常见词，"
            "也跳过人名、地名、机构名等专有名词。"
            "重点提考研阅读里的学术词、形近易混词和一词多义词。"
        ),
        "group_hint": (
            "用文章主题起一个简短的名字，如「人工智能与艺术」；"
            "如果能判断是考研哪年真题，就用「考研年份 第几篇」格式，如「2024考研 第1篇」。"
        ),
    },
    "ielts": {
        "exam_name": "雅思（IELTS）",
        "level_rule": (
            "只提雅思学术类（Academic）级别的词：跳过日常口语里已经掌握的常见词，"
            "也跳过人名、地名、机构名等专有名词。"
            "重点提学术写作和阅读里的正式词汇、固定搭配和同义替换词。"
        ),
        "group_hint": "用文章主题起一个简短的名字，如「人工智能与艺术」。",
    },
    "toefl": {
        "exam_name": "托福（TOEFL）",
        "level_rule": (
            "只提托福学术级别的词：跳过日常对话里已经掌握的常见词，"
            "也跳过人名、地名、机构名等专有名词。"
            "重点提学术讲座和阅读里的正式词汇、学科术语和同义替换词。"
        ),
        "group_hint": "用文章主题起一个简短的名字，如「人工智能与艺术」。",
    },
}


def get_extraction_prompt(exam: str | None) -> str:
    """按考试返回对应的提取 prompt；exam 为空或不在表里时默认 CET-6。"""
    key = (exam or "").strip().lower()
    profile = EXAM_PROFILES.get(key, EXAM_PROFILES["cet6"])
    return f"""你是 Muggle Word Cards 的词汇提取助手。用户是一名正在备考{profile["exam_name"]}的中国大学生。
你的任务：阅读下面 OCR 识别出的英文文章（可能含少量识别错误，请结合上下文理解），提取出值得做成单词卡的生词，
以 JSON 格式返回。只返回 JSON，不要加任何解释、不要用 markdown 代码块包裹。

## 提取标准（按重要性排序）
1. {profile["level_rule"]}
2. 题目词也要提：文章后面附带的题目（题干和选项）里的关键词同样提取，不只是正文里的词。
3. 数量控制在 10–30 个：宁缺毋滥，拿不准的词不要硬凑。
4. 每个词都要有「在文中出现」的依据，禁止凭空编词。

## 每个词必须包含的字段
- word：英文单词或短语原形（如 take a toll on、fluctuating）。
- phonetic：英式音标（IPA），如 /ˈflʌktʃueɪtɪŋ/。不会读的词宁可空着，也不要编一个错的。
- meaning：中文释义，必须贴合这篇文章的语境。不要给词典里与本文无关的义项。
  例如文中 "yield" 是「产量」的意思，就不要写「屈服」。
- phrase：例句，必须一字不改照抄原文里的原句（不要自己造句、不要改写）。
- hook：记忆钩子，一句话，帮记忆：可以是词根拆分、谐音、联想或与中文意思的挂钩，要简短生动。
- trap（可选）：这个词容易踩的坑，如易混词、反义陷阱、题目里的干扰项，没有就空着。

## 分组名
- group_label：给这批词起一个分组名，{profile["group_hint"]}

## 输出格式（严格遵守）
{{
  "group_label": "2023-12 第1套 P1 文章词",
  "words": [
    {{
      "word": "fluctuating",
      "phonetic": "/ˈflʌktʃueɪtɪŋ/",
      "meaning": "波动的；不稳定的",
      "phrase": "Fluctuating tax rates globally.",
      "hook": "flu-（流动）+ -ctuat：像水一样上下波动",
      "trap": "不要写成“流利的”，和 fluent 是两回事"
    }}
  ]
}}

## 最后检查
- 所有 phrase 都能在原文中找到原句吗？找不到的词删掉。
- meaning 和本文语境一致吗？不一致的改掉。
- 有没有混进太简单的词或专有名词？有就删掉。
"""


# 默认仍是 CET-6（老调用方不传 exam 时的行为不变）。
EXTRACTION_SYSTEM_PROMPT = get_extraction_prompt("cet6")

# Muggle Word Cards — DeepSeek 客户端
# -*- coding: utf-8 -*-
"""调 DeepSeek API 做生词提取。

安全红线：
- API Key 只从环境变量 DEEPSEEK_API_KEY 读，绝不写在代码里，
  也绝不接受前端传来的 Key（前端传 Key = 把账单交给任何人刷）。
- 计费按 token 算：一次提取约 2000 输入 + 3000 输出 tokens，
  用 deepseek-chat 大约 3 分钱。Kimi 同等任务约 2 毛 3，贵七八倍。
"""
from __future__ import annotations

import json
import os

import httpx

from .prompts import get_extraction_prompt


class LLMError(Exception):
    """调模型失败（网络错、配额错、返回的 JSON 解析失败）时抛这个错。"""


def _normalize_draft(data: dict) -> dict:
    """把模型返回的 JSON 整成固定形状：缺失字段补空串，非法词条丢掉。"""
    if not isinstance(data, dict):
        raise LLMError("模型返回的不是 JSON 对象")
    words = data.get("words")
    if not isinstance(words, list):
        raise LLMError("模型返回的 JSON 里没有 words 列表")

    clean_words = []
    for item in words:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word", "")).strip()
        meaning = str(item.get("meaning", "")).strip()
        if not word or not meaning:  # 词和释义是底线，缺一不可
            continue
        clean_words.append({
            "word": word,
            "phonetic": str(item.get("phonetic", "")).strip(),
            "meaning": meaning,
            "phrase": str(item.get("phrase", "")).strip(),
            "hook": str(item.get("hook", "")).strip(),
            "trap": str(item.get("trap", "")).strip(),
        })
    if not clean_words:
        raise LLMError("模型返回的 words 列表是空的（或没有一条包含 word 和 meaning）")
    return {
        "group_label": str(data.get("group_label", "")).strip(),
        "words": clean_words,
    }


class DeepSeekClient:
    """DeepSeek API 客户端（OpenAI 兼容接口）。

    model 默认 deepseek-chat（便宜够用）。想换更强的模型，
    在 .env 里改 DEEPSEEK_MODEL 即可，不用改代码。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY 未配置：请在 backend/.env 里填入从 "
                "https://platform.deepseek.com 申请的 Key（充值几块钱就够测很久）。"
            )
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    async def extract_words(self, passage_text: str, exam: str | None = None) -> dict:
        """输入 OCR 识别出的文章全文，返回 {"group_label": ..., "words": [...]}。

        exam 是用户定的考试（cet4 / cet6 / kaoyan / ielts / toefl），
        决定按哪个级别筛词；不传或传了未知值时默认 CET-6。
        JSON 解析失败时自动重试一次：第二次请求会把第一次的坏输出贴回去，
        明确要求模型只返回合法 JSON。两次都失败才抛 LLMError。
        """
        messages: list[dict] = [
            {"role": "system", "content": get_extraction_prompt(exam)},
            {
                "role": "user",
                "content": "以下是 OCR 识别出的英文文章（可能含少量识别错误，请按上下文理解）：\n\n" + passage_text,
            },
        ]

        last_error: Exception | None = None
        for _ in range(2):  # 最多两次：首次 + 一次重试
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.3,  # 低温度：提取要稳定、可复现，不要发散
                "response_format": {"type": "json_object"},  # 强制 JSON 输出
            }
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
            except httpx.HTTPError as exc:
                raise LLMError(f"连接 DeepSeek 失败（网络问题）：{exc}")

            if resp.status_code != 200:
                raise LLMError(f"DeepSeek 返回 {resp.status_code}：{resp.text[:300]}")

            try:
                content = resp.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"DeepSeek 返回结构异常：{exc}")

            try:
                return _normalize_draft(json.loads(content))
            except (json.JSONDecodeError, LLMError) as exc:
                last_error = exc
                # 把坏输出贴回去，要求只返合法 JSON，再试一次
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "你刚才的输出不是合法 JSON。请只返回合法 JSON，不要加任何解释、不要用 markdown 代码块包裹。",
                })

        raise LLMError(f"模型两次返回的都不是合法 JSON：{last_error}")

# Muggle Word Cards — 后端 API（FastAPI + SQLite）
# -*- coding: utf-8 -*-
"""接口一览：
- GET  /health                  健康检查
- POST /api/extract             上传 1-3 张图片 -> OCR -> DeepSeek 提取 -> 返回草稿（不保存）
- POST /api/import              把确认过的草稿存成新分组（自动跳过已在库里的词）
- GET  /api/groups              分组列表（含每组词数）
- GET  /api/groups/{id}/words   某分组下的全部单词
- POST /api/review/{word_id}    记录一次复习结果（认识/不认识），按阶梯排期
- GET  /api/review/due          今天到期的词
- GET  /api/wanted               通缉名单（连续答错 >= 2 次的词）

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
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db
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
DEFAULT_USER_ID = 1  # 单人使用：固定默认用户。以后加账号系统再按登录用户区分。

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


# ---------- 健康检查 ----------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "db": str(DB_PATH)}


# ---------- 提取：拍照 -> OCR -> 大模型（只出草稿，不保存） ----------

@app.post("/api/extract")
async def extract(
    request: Request,
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
def import_words(payload: ImportRequest) -> dict:
    group_id = db.create_group(DB_PATH, DEFAULT_USER_ID, payload.group_label, payload.source)
    imported: list[str] = []
    skipped: list[str] = []
    seen_in_batch: set[str] = set()
    for w in payload.words:
        key = w.word.strip().lower()
        if not key:
            continue
        if key in seen_in_batch or db.word_exists(DB_PATH, DEFAULT_USER_ID, w.word):
            skipped.append(w.word)  # 已在库里的词跳过，不动它原来的进度
            continue
        seen_in_batch.add(key)
        db.insert_word(
            DB_PATH,
            group_id=group_id,
            user_id=DEFAULT_USER_ID,
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
def list_groups() -> list[dict]:
    return db.list_groups(DB_PATH, DEFAULT_USER_ID)


@app.get("/api/groups/{group_id}/words")
def list_group_words(group_id: int) -> list[dict]:
    if not db.group_exists(DB_PATH, group_id):
        raise HTTPException(404, f"分组 {group_id} 不存在")
    return db.list_words_in_group(DB_PATH, group_id)


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
def review_word(word_id: int, payload: ReviewRequest) -> dict:
    word = db.get_word(DB_PATH, word_id)
    if word is None:
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
def due_today() -> list[dict]:
    return db.due_words(DB_PATH, DEFAULT_USER_ID)


@app.get("/api/wanted")
def wanted() -> list[dict]:
    return db.wanted_words(DB_PATH, DEFAULT_USER_ID)
