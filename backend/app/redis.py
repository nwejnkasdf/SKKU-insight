"""Redis client factory — 4 DB 분리 (default/rate_limit/queue/cache).

env-vars.md §Redis. 각 DB:
- 0 default: refresh meta, denylist, consent cache, onboarding lock, cold_start status
- 1 rate_limit: slowapi 카운터
- 2 queue: RQ + rq-scheduler 작업 큐
- 3 cache: 추천 캐시 등 큰 객체
"""
from __future__ import annotations

from typing import Literal

import redis.asyncio as aioredis

from app.config import get_settings

RedisDB = Literal["default", "rate_limit", "queue", "cache"]

_clients: dict[RedisDB, aioredis.Redis] = {}


def get_redis(db: RedisDB = "default") -> aioredis.Redis:
    """DB 분리 client. lifespan 에서 ping 검증 후 사용."""
    if db in _clients:
        return _clients[db]
    settings = get_settings()
    url = {
        "default": settings.REDIS_URL,
        "rate_limit": settings.REDIS_URL_RATE_LIMIT,
        "queue": settings.REDIS_URL_QUEUE,
        "cache": settings.REDIS_URL_CACHE,
    }[db]
    client = aioredis.from_url(url, decode_responses=True)
    _clients[db] = client
    return client


async def close_redis() -> None:
    """lifespan shutdown 에서 호출."""
    for client in _clients.values():
        await client.aclose()
    _clients.clear()


__all__ = ["RedisDB", "close_redis", "get_redis"]
