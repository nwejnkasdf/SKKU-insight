"""interest_decay worker — A6 daily decay cron 본문.

cron = `INTEREST_DECAY_CRON` (default `0 18 * * *` UTC = 03:00 KST).
사용자별 UserInterestState row 들에 active day 차이만큼 시간 감쇠 + 14-day boost 만료 차감.

RQ sync entrypoint — asyncio.run() 으로 async 구현 호출.
"""
from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.interest.config_loader import get_interest_params
from app.interest.decay import apply_decay_to_all_users
from app.redis import get_redis

logger = logging.getLogger("interest_decay_job")


async def _run() -> int:
    settings = get_settings()
    redis: aioredis.Redis = get_redis("default")
    async with AsyncSessionLocal() as db:
        params = await get_interest_params(redis, db)
        users_processed = await apply_decay_to_all_users(
            db, redis, params=params, settings=settings
        )
    logger.info("interest_decay_job: users_processed=%d", users_processed)
    return users_processed


def interest_decay_job() -> None:
    """RQ sync entrypoint. scheduler.py 가 등록."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


__all__ = ["interest_decay_job"]
