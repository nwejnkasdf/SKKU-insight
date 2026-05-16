"""Event idempotency — payload hash + (user_id, client_request_id) UNIQUE.

같은 (user_id, client_request_id) 재호출 시:
- payload_hash 일치 → 200 + 기존 UserEvent row 반환
- payload_hash 불일치 → 409 EVENT_DUPLICATE (다른 payload 가 같은 key 재사용)

DB UNIQUE(user_id, client_request_id) 가 1차 SOR. Redis 캐시는 hot path RTT 단축용.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import RedisKey
from app.db.models import UserEvent


class IdempotencyOutcome(str, Enum):
    NEW = "new"
    DUPLICATE_MATCH = "duplicate_match"
    DUPLICATE_MISMATCH = "duplicate_mismatch"


@dataclass(frozen=True)
class IdempotencyLookup:
    """check_idempotent 의 응답 type."""

    outcome: IdempotencyOutcome
    existing_event_id: UUID | None
    existing_occurred_at: datetime | None
    existing_created_at: datetime | None


def compute_payload_hash(
    *,
    event_type: str,
    document_id: UUID | None,
    cso_topic_id: UUID | None,
    leaf_topic_id: UUID | None,
    dwell_ms: int | None,
    occurred_at: datetime,
) -> str:
    """sha256[:64] hex. canonical JSON (sorted keys, isoformat datetime).

    DB user_event.payload_hash 컬럼은 String(64) — 본 함수는 정확히 64 hex char 반환.
    """
    payload: dict[str, Any] = {
        "event_type": event_type,
        "document_id": str(document_id) if document_id else None,
        "cso_topic_id": str(cso_topic_id) if cso_topic_id else None,
        "leaf_topic_id": str(leaf_topic_id) if leaf_topic_id else None,
        "dwell_ms": dwell_ms,
        "occurred_at": occurred_at.isoformat(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def check_idempotent(
    db: AsyncSession,
    redis: aioredis.Redis,
    *,
    user_id: UUID,
    client_request_id: str,
    payload_hash: str,
) -> IdempotencyLookup:
    """(user_id, client_request_id) row 존재 여부 + payload_hash 비교.

    1) Redis 캐시 hit → payload_hash 비교 (hot path)
    2) miss → DB SELECT UNIQUE row → 분기

    Redis 캐시 형식: `event:dup:{user_id}:{request_id}` = payload_hash:event_id 의 `:` 결합.
    """
    cache_key = RedisKey.event_duplicate_cache(user_id, client_request_id)
    cached_raw = await redis.get(cache_key)
    if cached_raw is not None:
        cached = (
            cached_raw if isinstance(cached_raw, str) else cached_raw.decode()
        )
        # 형식: "hash:event_id"
        try:
            cached_hash, cached_event_id = cached.split(":", 1)
        except ValueError:
            cached_hash, cached_event_id = "", ""
        if cached_hash == payload_hash and cached_event_id:
            return IdempotencyLookup(
                outcome=IdempotencyOutcome.DUPLICATE_MATCH,
                existing_event_id=UUID(cached_event_id),
                existing_occurred_at=None,
                existing_created_at=None,
            )
        if cached_hash and cached_hash != payload_hash:
            return IdempotencyLookup(
                outcome=IdempotencyOutcome.DUPLICATE_MISMATCH,
                existing_event_id=UUID(cached_event_id) if cached_event_id else None,
                existing_occurred_at=None,
                existing_created_at=None,
            )
    # DB lookup
    row = (
        await db.execute(
            select(
                UserEvent.event_id,
                UserEvent.payload_hash,
                UserEvent.occurred_at,
                UserEvent.created_at,
            ).where(
                UserEvent.user_id == user_id,
                UserEvent.client_request_id == client_request_id,
            )
        )
    ).first()
    if row is None:
        return IdempotencyLookup(
            outcome=IdempotencyOutcome.NEW,
            existing_event_id=None,
            existing_occurred_at=None,
            existing_created_at=None,
        )
    if row.payload_hash == payload_hash:
        return IdempotencyLookup(
            outcome=IdempotencyOutcome.DUPLICATE_MATCH,
            existing_event_id=row.event_id,
            existing_occurred_at=row.occurred_at,
            existing_created_at=row.created_at,
        )
    return IdempotencyLookup(
        outcome=IdempotencyOutcome.DUPLICATE_MISMATCH,
        existing_event_id=row.event_id,
        existing_occurred_at=row.occurred_at,
        existing_created_at=row.created_at,
    )


async def store_idempotent(
    redis: aioredis.Redis,
    *,
    user_id: UUID,
    client_request_id: str,
    payload_hash: str,
    event_id: UUID,
    ttl_seconds: int,
) -> None:
    """이벤트 INSERT 후 Redis 캐시 등록. client retry 시 200 응답 hot path."""
    cache_key = RedisKey.event_duplicate_cache(user_id, client_request_id)
    await redis.setex(cache_key, ttl_seconds, f"{payload_hash}:{event_id}")


__all__ = [
    "IdempotencyLookup",
    "IdempotencyOutcome",
    "check_idempotent",
    "compute_payload_hash",
    "store_idempotent",
]
