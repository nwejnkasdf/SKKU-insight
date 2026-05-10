"""Consent active 상태 Redis 캐시 (60s TTL).

concurrency.md §7. invalidate: revoke / account-deletion / 새 consent INSERT 시 호출.
"""
from __future__ import annotations

from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import RedisKey
from app.db.models import UserConsent


async def is_consent_active(
    user_id: UUID, redis: aioredis.Redis, db: AsyncSession
) -> bool:
    """personalization consent 활성 여부. 60s Redis 캐시."""
    key = RedisKey.consent_active_cache(user_id)
    cached = await redis.get(key)
    if cached is not None:
        return cached == "1"
    stmt = select(UserConsent).where(
        UserConsent.user_id == user_id,
        UserConsent.consent_type == "personalization",
        UserConsent.revoked_at.is_(None),
    )
    row = (await db.execute(stmt)).scalars().first()
    active = row is not None
    settings = get_settings()
    await redis.setex(key, settings.CONSENT_CACHE_TTL_SECONDS, "1" if active else "0")
    return active


async def invalidate_consent_cache(user_id: UUID, redis: aioredis.Redis) -> None:
    """revoke / 새 consent / account-deletion 시 호출."""
    await redis.delete(RedisKey.consent_active_cache(user_id))


__all__ = ["invalidate_consent_cache", "is_consent_active"]
