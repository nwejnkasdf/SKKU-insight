"""interest_decay worker — A6 daily decay cron 본문.

cron = `INTEREST_DECAY_CRON` (default `0 18 * * *` UTC = 03:00 KST).
사용자별 UserInterestState row 들에 active day 차이만큼 시간 감쇠 + 14-day boost 만료 차감.

(C-72, 2026-05-26) boost trace 14d 자연 만료 통합 — `UserCSOTraversal` 의
`origin='onboarding_boost'` 활성 trace 중 `(user.active_day_counter -
started_active_day) >= INTEREST_BOOST_EXPIRY_ACTIVE_DAYS (=14)` 인 row DELETE.
직전 (C-62 결정 #11) 의 "첫 behavioral 신호 시 모든 boost DELETE" 정책 폐기 (C-71/C-72) —
cluster 별 boost 가 14 active days 동안 자연 유지 → 사용자 명시 선택 narrative 정합.

RQ sync entrypoint — asyncio.run() 으로 async 구현 호출.
"""
from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import AsyncSessionLocal
from app.interest.config_loader import get_interest_params
from app.interest.decay import apply_decay_to_all_users
from app.redis import get_redis

logger = logging.getLogger("interest_decay_job")


async def _expire_onboarding_boost_traces(
    db: AsyncSession, settings: Settings
) -> int:
    """(C-72, 2026-05-26) boost trace 14 active days 자연 만료.

    cso-topic-traversal.md §1.2 "14 active day 한정 prior boost" narrative —
    bootstrap_interest_state 가 INSERT 한 boost trace (path=[cluster_root],
    origin='onboarding_boost') 가 사용자의 active_day_counter 가 started_active_day +
    INTEREST_BOOST_EXPIRY_ACTIVE_DAYS (=14) 이상 경과 시 DELETE. 사용자가 그 cluster
    영역 doc click 했다면 daily_trace_update 의 `ingest_event` 가 origin →
    'behavioral' 로 promote (path 보존) — 본 함수에서 더 이상 boost 아니므로 영향 X.

    조건: origin='onboarding_boost' AND status='active' AND
          (user.active_day_counter - trace.started_active_day) >= boost_days

    return: 삭제된 row 수 (logger 통계).
    """
    boost_days = settings.INTEREST_BOOST_EXPIRY_ACTIVE_DAYS
    stmt = text(
        """
        DELETE FROM user_cso_traversal AS t
        USING "user" AS u
        WHERE t.user_id = u.user_id
          AND t.origin = 'onboarding_boost'
          AND t.status = 'active'
          AND (u.active_day_counter - t.started_active_day) >= :boost_days
        """
    )
    result = await db.execute(stmt, {"boost_days": boost_days})
    return int(result.rowcount or 0)


async def _run() -> int:
    settings = get_settings()
    redis: aioredis.Redis = get_redis("default")
    async with AsyncSessionLocal() as db:
        params = await get_interest_params(redis, db)
        users_processed = await apply_decay_to_all_users(
            db, redis, params=params, settings=settings
        )
        # (C-72) boost trace 14d 자연 만료. apply_decay_to_all_users 가 commit 했어도
        # 본 DELETE 는 별개 트랜잭션 OK (단일 SQL atomic). 명시 commit.
        boost_expired = await _expire_onboarding_boost_traces(db, settings)
        await db.commit()
    logger.info(
        "interest_decay_job: users_processed=%d boost_traces_expired=%d",
        users_processed,
        boost_expired,
    )
    return users_processed


def interest_decay_job() -> None:
    """RQ sync entrypoint. scheduler.py 가 등록."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


__all__ = ["interest_decay_job"]
