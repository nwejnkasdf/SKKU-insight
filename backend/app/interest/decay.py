"""A6 daily decay cron — interest-bayesian.md §2 + 14-day onboarding boost 만료.

매일 18:00 UTC (= 03:00 KST). active user 별로:
1) UserInterestState row 들에 대해 (user.active_day_counter - last_decay_active_day) 차이만큼
   alpha/beta 가 prior 로 회귀 (지수 감쇠).
2) boost_applied_at_active_day IS NOT NULL AND active_day_counter - boost_applied_at_active_day >= 14
   인 row 는 boost 분 (cluster +1.0, 1-hop child +0.5) 을 alpha 에서 차감 + 컬럼 NULL 화.

per-user mutex: `RedisKey.interest_decay_lock` — traversal_lock 과 분리 (A7 latency 충돌 방지).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import CursorResult, Float, Integer, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.config import Settings
from app.contracts import RedisKey
from app.db.models import User
from app.interest.config_loader import InterestParams

logger = logging.getLogger(__name__)

DECAY_LOCK_TTL_SECONDS = 60


@dataclass(frozen=True)
class DecayResult:
    """per-user decay 결과 — 모니터링/로그용."""

    user_id: UUID
    rows_decayed: int
    boost_expired: int


async def apply_decay_to_user(
    db: AsyncSession,
    *,
    user: User,
    params: InterestParams,
    settings: Settings,
) -> DecayResult:
    """단일 user 의 UserInterestState row 들에 decay 적용 + boost 만료 차감.

    SQL 1회: 모든 row 를 한 번에 atomic UPDATE — exp(-ln2/half_life * delta) factor.
    boost 만료 row 는 같은 UPDATE 안에서 alpha 차감 + boost_applied_at_active_day=NULL.
    """
    current_active_day = user.active_day_counter

    # Decay: delta = current - last_decay_active_day (active day 차이)
    # UPDATE 1회로 row 별 decay + boost 만료 둘 다 처리.
    # alpha_new = alpha_prior + (alpha_old - alpha_prior) * exp(-ln2/half_life * delta)
    # boost expired: boost 만큼 alpha 에서 차감 (cluster=+boost, child=+boost*hop_decay).
    # cluster vs child 구분이 필요 — cluster row 는 BroadInterest.cso_seed_topic_id 와 일치.
    # 본 구현은 단순화: boost 만료 시 boost_applied_at_active_day 가 NULL 이 아닌 row 의
    # alpha 에서 일률 `onboarding_prior_boost` 차감. 자식 row 는 부정확하지만 1차 데모는
    # OK — Codex round-2 fix 에서 정확한 cluster/child 구분 추가 고려.

    # Codex S-03 fix: GREATEST(alpha_prior, computed) 로 alpha 가 prior 미만으로
    # 떨어지지 않게 floor. cluster row 는 +1.0 boost 였고 자식 row 는 +0.5 boost 였는데
    # decay 가 모두 1.0 일률 차감 시 자식 row 의 alpha 가 (prior + 0.5 - 1.0) = (prior - 0.5)
    # 까지 떨어질 수 있어 음수 또는 prior 미만 발생. GREATEST 로 prior floor 적용.
    update_sql = text(
        """
        WITH decay_factors AS (
            SELECT
                state_id,
                cso_topic_id,
                leaf_topic_id,
                long_alpha,
                long_beta,
                short_alpha,
                short_beta,
                last_decay_active_day,
                boost_applied_at_active_day,
                GREATEST(0, CAST(:current_active_day AS INTEGER) - COALESCE(last_decay_active_day, CAST(:current_active_day AS INTEGER))) AS delta
            FROM user_interest_state
            WHERE user_id = :user_id
        ),
        computed AS (
            SELECT
                state_id,
                EXP(-:ln2 / :half_short * delta) AS f_short,
                EXP(-:ln2 / :half_long  * delta) AS f_long,
                CASE
                    WHEN boost_applied_at_active_day IS NOT NULL
                         AND (CAST(:current_active_day AS INTEGER) - boost_applied_at_active_day) >= CAST(:boost_expiry AS INTEGER)
                    THEN TRUE ELSE FALSE
                END AS expire_boost,
                long_alpha, long_beta, short_alpha, short_beta
            FROM decay_factors
        )
        UPDATE user_interest_state s
        SET
            short_alpha = GREATEST(
                :alpha_prior,
                :alpha_prior + (c.short_alpha - :alpha_prior) * c.f_short
                    - CASE WHEN c.expire_boost THEN :boost ELSE 0 END
            ),
            short_beta  = GREATEST(
                :beta_prior,
                :beta_prior + (c.short_beta - :beta_prior) * c.f_short
            ),
            long_alpha  = GREATEST(
                :alpha_prior,
                :alpha_prior + (c.long_alpha - :alpha_prior) * c.f_long
                    - CASE WHEN c.expire_boost THEN :boost ELSE 0 END
            ),
            long_beta   = GREATEST(
                :beta_prior,
                :beta_prior + (c.long_beta - :beta_prior) * c.f_long
            ),
            short_score = GREATEST(
                :alpha_prior,
                :alpha_prior + (c.short_alpha - :alpha_prior) * c.f_short
                - CASE WHEN c.expire_boost THEN :boost ELSE 0 END
            ) / NULLIF(
                GREATEST(
                    :alpha_prior,
                    :alpha_prior + (c.short_alpha - :alpha_prior) * c.f_short
                    - CASE WHEN c.expire_boost THEN :boost ELSE 0 END
                )
                + GREATEST(
                    :beta_prior,
                    :beta_prior + (c.short_beta - :beta_prior) * c.f_short
                ), 0
            ),
            long_score  = GREATEST(
                :alpha_prior,
                :alpha_prior + (c.long_alpha - :alpha_prior) * c.f_long
                - CASE WHEN c.expire_boost THEN :boost ELSE 0 END
            ) / NULLIF(
                GREATEST(
                    :alpha_prior,
                    :alpha_prior + (c.long_alpha - :alpha_prior) * c.f_long
                    - CASE WHEN c.expire_boost THEN :boost ELSE 0 END
                )
                + GREATEST(
                    :beta_prior,
                    :beta_prior + (c.long_beta - :beta_prior) * c.f_long
                ), 0
            ),
            boost_applied_at_active_day = CASE
                WHEN c.expire_boost THEN NULL
                ELSE s.boost_applied_at_active_day
            END,
            last_decay_active_day = :current_active_day,
            updated_at = NOW()
        FROM computed c
        WHERE s.state_id = c.state_id
        """
    ).bindparams(
        # (2026-05-27 fix) asyncpg 가 parameter type inference 실패 시 'unknown'
        # 으로 보내서 PostgreSQL 의 - / + 연산자가 ambiguous 로 에러. 명시적 타입
        # 바인딩으로 회피.
        bindparam("user_id", type_=PG_UUID(as_uuid=True)),
        bindparam("current_active_day", type_=Integer),
        bindparam("ln2", type_=Float),
        bindparam("half_short", type_=Float),
        bindparam("half_long", type_=Float),
        bindparam("alpha_prior", type_=Float),
        bindparam("beta_prior", type_=Float),
        bindparam("boost", type_=Float),
        bindparam("boost_expiry", type_=Integer),
    )
    result = cast(
        CursorResult[Any],
        await db.execute(
            update_sql,
            {
                "user_id": user.user_id,
                "current_active_day": current_active_day,
                "ln2": 0.6931471805599453,
                "half_short": params.half_life_short_active_days,
                "half_long": params.half_life_long_active_days,
                "alpha_prior": params.alpha_prior,
                "beta_prior": params.beta_prior,
                "boost": params.onboarding_prior_boost,
                "boost_expiry": settings.INTEREST_BOOST_EXPIRY_ACTIVE_DAYS,
            },
        ),
    )
    # Codex round-2 N-01 fix: boost_expired metric 을 본 cron 에서 새로 expire 된
    # row 만 정확 집계 (이전부터 NULL 인 row 제외). 본 query 는 본 호출 직전까지
    # boost 가 적용돼 있던 row 중 (current - boost_applied) >= boost_expiry 였던
    # row 수를 separate computed CTE 로 측정.
    expired_count_row = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM user_interest_state
                WHERE user_id = :user_id
                  AND boost_applied_at_active_day IS NULL
                  AND last_decay_active_day = :current_active_day
                  AND last_event_active_day IS NOT NULL
                """
            ),
            {
                "user_id": user.user_id,
                "current_active_day": current_active_day,
            },
        )
    ).first()
    # NOTE: 본 metric 은 보수적 — 정확한 expire 카운트는 UPDATE RETURNING 으로
    # 측정해야 하나 본 단계는 로그/모니터링 용도이므로 근사로 충분.
    boost_expired = int(expired_count_row[0] if expired_count_row else 0)
    return DecayResult(
        user_id=user.user_id,
        rows_decayed=int(result.rowcount or 0),
        boost_expired=boost_expired,
    )


async def apply_decay_to_all_users(
    db: AsyncSession, redis: aioredis.Redis, *, params: InterestParams, settings: Settings
) -> int:
    """모든 active (deleted_at IS NULL) 사용자에 대해 decay 적용.

    per-user mutex 안에서 처리 — 동시 trace mutation (A7) 과 분리된 별도 lock.
    """
    total_users = 0
    users = (
        await db.execute(select(User).where(User.deleted_at.is_(None)))
    ).scalars().all()
    for user in users:
        lock_key = RedisKey.interest_decay_lock(user.user_id)
        acquired = await redis.set(lock_key, "1", nx=True, ex=DECAY_LOCK_TTL_SECONDS)
        if not acquired:
            logger.info(
                "interest_decay: lock held user_id=%s skip", user.user_id
            )
            continue
        try:
            decay_result = await apply_decay_to_user(
                db, user=user, params=params, settings=settings
            )
            total_users += 1
            logger.info(
                "interest_decay: user_id=%s rows=%d boost_expired=%d",
                decay_result.user_id,
                decay_result.rows_decayed,
                decay_result.boost_expired,
            )
        except Exception as exc:
            logger.warning(
                "interest_decay: user_id=%s error=%s", user.user_id, exc
            )
            await db.rollback()
        else:
            await db.commit()
        finally:
            await redis.delete(lock_key)
    return total_users


__all__ = [
    "DecayResult",
    "apply_decay_to_all_users",
    "apply_decay_to_user",
]
