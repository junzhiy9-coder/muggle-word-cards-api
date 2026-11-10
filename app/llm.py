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

from .prompts import EXTRACTION_SYSTEM_PROMPT


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

    async def extract_words(self, passage_text: str) -> dict:
        """输入 OCR 识别出的文章全文，返回 {"group_label": ..., "words": [...]}。

        JSON 解析失败时自动重试一次：第二次请求会把第一次的坏输出贴回去，
        明确要求模型只返回合法 JSON。两次都失败才抛 LLMError。
        """
        messages: list[dict] = [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
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
