"""LLM 동시성 + 토큰 budget 가드.

전역 `asyncio.Semaphore(LLM_MAX_CONCURRENT)` + per-user dict[str, Semaphore].
Redis `llm:tokens:{date}` INCR 로 일일 토큰 사용량 추적.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import redis.asyncio as aioredis

from app.config import get_settings
from app.contracts import RedisKey

_settings = get_settings()
_global_sem = asyncio.Semaphore(_settings.LLM_MAX_CONCURRENT)
_user_sems: dict[str, asyncio.Semaphore] = {}


def _per_user_sem(user_id: str) -> asyncio.Semaphore:
    sem = _user_sems.get(user_id)
    if sem is None:
        sem = asyncio.Semaphore(_settings.LLM_MAX_CONCURRENT_PER_USER)
        _user_sems[user_id] = sem
    return sem


@asynccontextmanager
async def acquire_slot(user_id: str | None):  # type: ignore[no-untyped-def]
    """전역 + per-user semaphore 둘 다 acquire."""
    async with _global_sem:
        if user_id:
            async with _per_user_sem(user_id):
                yield
        else:
            yield


async def check_token_budget(redis: aioredis.Redis) -> bool:
    """일일 토큰 예산 잔여 확인. True = 호출 OK."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    raw = await redis.get(RedisKey.llm_token_usage_daily(today))
    used = int(raw) if raw else 0
    return used < _settings.LLM_DAILY_TOKEN_BUDGET


async def record_token_usage(
    tokens: int, redis: aioredis.Redis, *, ttl_seconds: int = 86_400 * 3
) -> None:
    """일일 토큰 사용량 INCR. TTL 3일 (감사 추적)."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    key = RedisKey.llm_token_usage_daily(today)
    await redis.incrby(key, tokens)
    await redis.expire(key, ttl_seconds)


__all__ = ["acquire_slot", "check_token_budget", "record_token_usage"]
