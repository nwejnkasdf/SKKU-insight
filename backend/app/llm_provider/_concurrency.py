"""LLM 동시성 가드 — Redis 기반 분산 semaphore (multi-worker 안전).

decision-backlog C-19 + C-20: asyncio.Semaphore 는 per-process. uvicorn `--workers N`
또는 다중 컨테이너 환경에서 전역 캡 (LLM_MAX_CONCURRENT) 보장 불가. Redis 의
INCR/DECR + TTL + Lua atomic check 패턴으로 분산 환경에서도 글로벌 한도 강제.

acquire 패턴 (Lua atomic):
  1. global counter < LLM_MAX_CONCURRENT? 아니면 reject
  2. user counter < LLM_MAX_CONCURRENT_PER_USER? 아니면 global rollback + reject
  3. 통과 시 둘 다 INCR + EXPIRE (crash 시 자연 해소용 60s TTL)

release (Lua atomic):
  - global DECR (0 미만 방지 floor 0)
  - user DECR (있으면, floor 0)

acquire 실패 시 짧은 sleep 후 재시도. LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS 초과 시
LLMBudgetExceeded 와 동일 처리 (호출자가 fallback 경로 진입).
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as aioredis

from app.config import get_settings
from app.contracts import RedisKey
from app.llm_provider.protocol import LLMBudgetExceeded
from app.redis import get_redis


# crash 시 자연 해소 위한 counter TTL (acquire 후 release 안 되도 자연 0 으로 복귀).
# (Codex R3-NEW-C1 fix) LLM_REQUEST_TIMEOUT_SECONDS 변경에 동적 대응 — 60s 가 LLM
# 단일 호출 시간 (web_search 등) 보다 짧으면 counter 가 호출 도중 EXPIRE 되어 cap 깨짐.
# `_counter_ttl_seconds()` 가 호출 시점 settings 기반 계산 (caching X — env 변경 즉시 반영).
def _counter_ttl_seconds() -> int:
    """LLM_REQUEST_TIMEOUT_SECONDS + 30s 여유. floor 60s."""
    settings = get_settings()
    return max(60, settings.LLM_REQUEST_TIMEOUT_SECONDS + 30)


_RETRY_BACKOFF_SECONDS = 0.05  # acquire 재시도 간격
_RETRY_BACKOFF_MAX_SECONDS = 0.5

# Lua: global + (옵션) user counter 동시 atomic 검사·증가.
# KEYS[1] = global counter
# KEYS[2] = user counter (없으면 빈 문자열 — Lua 가 분기)
# ARGV[1] = global limit (str int)
# ARGV[2] = user limit (str int, 없으면 빈 문자열)
# ARGV[3] = counter TTL (str int)
# 반환: 1=acquired, 0=would_exceed
_LUA_ACQUIRE = """
local global_limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[3])
local global_current = tonumber(redis.call('GET', KEYS[1]) or '0')
if global_current >= global_limit then
    return 0
end
if KEYS[2] ~= '' and ARGV[2] ~= '' then
    local user_limit = tonumber(ARGV[2])
    local user_current = tonumber(redis.call('GET', KEYS[2]) or '0')
    if user_current >= user_limit then
        return 0
    end
    redis.call('INCR', KEYS[2])
    redis.call('EXPIRE', KEYS[2], ttl)
end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ttl)
return 1
"""

# Lua: release. floor 0 (음수 방지).
# KEYS[1] = global counter
# KEYS[2] = user counter (없으면 빈 문자열)
_LUA_RELEASE = """
local global_current = tonumber(redis.call('GET', KEYS[1]) or '0')
if global_current and global_current > 0 then
    redis.call('DECR', KEYS[1])
end
if KEYS[2] ~= '' then
    local user_current = tonumber(redis.call('GET', KEYS[2]) or '0')
    if user_current and user_current > 0 then
        redis.call('DECR', KEYS[2])
    end
end
return 1
"""


async def _acquire_slot_distributed(
    user_id: UUID | str | None, redis: aioredis.Redis
) -> None:
    """분산 semaphore acquire. timeout 초과 시 LLMBudgetExceeded raise."""
    settings = get_settings()
    global_key = RedisKey.llm_global_active_count()
    user_key = (
        RedisKey.llm_user_active_count(user_id if isinstance(user_id, UUID) else UUID(str(user_id)))
        if user_id
        else ""
    )
    deadline = time.monotonic() + settings.LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS
    backoff = _RETRY_BACKOFF_SECONDS
    counter_ttl = _counter_ttl_seconds()
    while True:
        acquired = await redis.eval(  # type: ignore[misc]
            _LUA_ACQUIRE,
            2,
            global_key,
            user_key,
            str(settings.LLM_MAX_CONCURRENT),
            str(settings.LLM_MAX_CONCURRENT_PER_USER) if user_id else "",
            str(counter_ttl),
        )
        if int(acquired) == 1:
            return
        if time.monotonic() >= deadline:
            raise LLMBudgetExceeded(
                f"semaphore_timeout: global={global_key} user={user_key or 'n/a'}"
            )
        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, _RETRY_BACKOFF_MAX_SECONDS)


async def _release_slot_distributed(
    user_id: UUID | str | None, redis: aioredis.Redis
) -> None:
    """분산 semaphore release. floor 0."""
    global_key = RedisKey.llm_global_active_count()
    user_key = (
        RedisKey.llm_user_active_count(user_id if isinstance(user_id, UUID) else UUID(str(user_id)))
        if user_id
        else ""
    )
    await redis.eval(  # type: ignore[misc]
        _LUA_RELEASE,
        2,
        global_key,
        user_key,
    )


@asynccontextmanager
async def acquire_slot(user_id: UUID | str | None):  # type: ignore[no-untyped-def]
    """LLM 호출 직전 acquire, 호출 후 release.

    multi-worker 안전 — 모든 process 가 같은 Redis counter 를 공유.
    """
    redis = get_redis("default")
    await _acquire_slot_distributed(user_id, redis)
    try:
        yield
    finally:
        await _release_slot_distributed(user_id, redis)


async def check_token_budget(redis: aioredis.Redis) -> bool:
    """일일 토큰 예산 잔여 확인. True = 호출 OK."""
    settings = get_settings()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    raw = await redis.get(RedisKey.llm_token_usage_daily(today))
    used = int(raw) if raw else 0
    return used < settings.LLM_DAILY_TOKEN_BUDGET


async def record_token_usage(
    tokens: int, redis: aioredis.Redis, *, ttl_seconds: int = 86_400 * 3
) -> None:
    """일일 토큰 사용량 INCR. TTL 3일 (감사 추적)."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    key = RedisKey.llm_token_usage_daily(today)
    await redis.incrby(key, tokens)
    await redis.expire(key, ttl_seconds)


__all__ = ["acquire_slot", "check_token_budget", "record_token_usage"]
