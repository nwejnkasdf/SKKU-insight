"""X-Idempotency-Key 헤더 처리 (api-conventions.md §3).

FastAPI Depends 패턴. `/onboarding/interests` POST 강제 (있으면 응답 캐시 1h, 없으면 single-flight lock fallback).
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import Header


async def get_idempotency_key(
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> str | None:
    """헤더 값 (있으면 36자 UUID 가정). 검증은 service 가 수행."""
    if x_idempotency_key is not None:
        # 길이 6-128 자만 허용 (UUID 36자 + 다른 형식 여유)
        if not (6 <= len(x_idempotency_key) <= 128):
            return None
    return x_idempotency_key


def _idempotency_cache_key(scope: str, user_id: UUID, key: str) -> str:
    return f"idemp:{scope}:{user_id}:{key}"


async def lookup_idempotent_response(
    scope: str, user_id: UUID, key: str | None, redis: aioredis.Redis
) -> dict[str, Any] | None:
    """기존 응답이 있으면 반환. 없으면 None."""
    if not key:
        return None
    cached = await redis.get(_idempotency_cache_key(scope, user_id, key))
    if cached is None:
        return None
    try:
        result = json.loads(cached)
    except (json.JSONDecodeError, TypeError):
        return None
    return result if isinstance(result, dict) else None


async def store_idempotent_response(
    scope: str,
    user_id: UUID,
    key: str | None,
    payload: dict[str, Any],
    redis: aioredis.Redis,
    *,
    ttl_seconds: int = 3600,
) -> None:
    """응답 캐싱 (TTL 기본 1h)."""
    if not key:
        return
    await redis.setex(
        _idempotency_cache_key(scope, user_id, key),
        ttl_seconds,
        json.dumps(payload, default=str),
    )


__all__ = [
    "get_idempotency_key",
    "lookup_idempotent_response",
    "store_idempotent_response",
]
