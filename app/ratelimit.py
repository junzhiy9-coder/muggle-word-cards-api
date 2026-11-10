# Muggle Word Cards — 简易限流：防止别人刷爆你的 DeepSeek 账单
# -*- coding: utf-8 -*-
"""为什么需要这个：/api/extract 每次调用都要花真钱调大模型（约 3 分钱/次）。
如果接口完全公开，有人写个循环一晚上就能刷掉几百块。
所以按 IP 计每天调用次数，默认 20 次/天/IP，超过直接返回 429。

注意：这是内存版实现，服务重启后计数清零；单机够用。
以后用户量大了，换成 Redis 计数（把 _counts 换成 redis incr + expire 即可），
接口（allow / remaining）不用变。
"""
from __future__ import annotations

import threading
from datetime import date

from fastapi import Request


class DailyRateLimiter:
    def __init__(self, limit_per_day: int = 20):
        self.limit = limit_per_day
        self._counts: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """今天该 key 还能调用吗？能则计数 +1 并返回 True，否则返回 False。"""
        today = date.today().isoformat()
        with self._lock:
            # 顺手清理过期日期的计数，防止字典无限变大
            for old_key in [k for k in self._counts if k[1] != today]:
                del self._counts[old_key]
            count = self._counts.get((key, today), 0)
            if count >= self.limit:
                return False
            self._counts[(key, today)] = count + 1
            return True

    def remaining(self, key: str) -> int:
        today = date.today().isoformat()
        with self._lock:
            return max(0, self.limit - self._counts.get((key, today), 0))


def get_client_ip(request: Request) -> str:
    """取客户端 IP。经过反向代理（Nginx/Render）时，真实 IP 在 X-Forwarded-For 里，
    取第一个；直连时用连接地址。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
