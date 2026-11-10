# Muggle Word Cards — 账号系统：密码哈希 + JWT
# -*- coding: utf-8 -*-
"""密码用 bcrypt 直接哈希（不自己拼盐、不存明文）；
登录态用 JWT（PyJWT，HS256，有效期 30 天）。

密钥 JWT_SECRET 从环境变量读。没配时进程内随机生成一个并打印醒目警告：
这样本地开发不用配也能跑，但服务一重启之前签发的 token 全部失效，
所以 Render 上必须在环境变量里配一个固定值。
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

ALGORITHM = "HS256"
TOKEN_TTL_DAYS = 30

_SECRET = os.getenv("JWT_SECRET")
if _SECRET:
    SECRET = _SECRET
else:
    SECRET = secrets.token_urlsafe(32)
    print(
        "⚠️  WARNING: JWT_SECRET 未设置，已随机生成一个临时密钥；"
        "服务重启后之前签发的 token 全部失效。"
        "生产环境请在 .env 或 Render 环境变量里设置 JWT_SECRET。"
    )


def hash_password(password: str) -> str:
    """明文密码 -> bcrypt 哈希字符串（入库只存这个）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码和库里的哈希。哈希格式不对也返回 False，不抛异常。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(user_id: int) -> str:
    """给用户签一个 30 天有效的 token。payload 只有用户 id，不放敏感信息。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(days=TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> int:
    """验 token，返回用户 id。token 无效、过期、被篡改时抛 jwt.PyJWTError。"""
    payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    return int(payload["sub"])
