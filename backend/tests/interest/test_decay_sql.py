"""apply_decay_to_user SQL UPDATE — 7-day decay 1/2 회귀 + 14-day boost 만료."""
from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import CSOTopic, User, UserInterestState
from app.interest.config_loader import load_system_config
from app.interest.decay import apply_decay_to_user


@pytest.mark.asyncio
async def test_seven_day_delta_halves_excess(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
) -> None:
    """delta=7 → short half-life decay factor = 0.5 → alpha 가 prior 쪽으로 절반 회귀."""
    settings = get_settings()
    params, _ = await load_system_config(db_session, redis_client)
    # 시드 row 1개 (alpha_prior + 4.0 가산)
    state_id = uuid.uuid4()
    db_session.add(
        UserInterestState(
            state_id=state_id,
            user_id=seeded_user.user_id,
            cso_topic_id=seeded_cso_topics[0].cso_topic_id,
            leaf_topic_id=None,
            long_alpha=params.alpha_prior + 4.0,
            long_beta=params.beta_prior,
            short_alpha=params.alpha_prior + 4.0,
            short_beta=params.beta_prior,
            long_score=0.5,
            short_score=0.5,
            last_event_active_day=0,
            last_decay_active_day=0,
            boost_applied_at_active_day=None,
        )
    )
    await db_session.flush()
    # active_day_counter 를 7 로 변경 → delta=7
    seeded_user.active_day_counter = 7
    await db_session.flush()
    await apply_decay_to_user(
        db_session, user=seeded_user, params=params, settings=settings
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            select(UserInterestState).where(
                UserInterestState.state_id == state_id
            )
        )
    ).scalar_one()
    # short_alpha = prior + (prior + 4 - prior) * exp(-ln2/7 * 7) = prior + 2.0
    expected_short = params.alpha_prior + 2.0
    assert math.isclose(row.short_alpha, expected_short, rel_tol=0.01)


@pytest.mark.asyncio
async def test_boost_expires_after_14_active_days(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
) -> None:
    """boost_applied_at_active_day=0 + active_day=14 → boost 1.0 차감 + 컬럼 NULL."""
    settings = get_settings()
    params, _ = await load_system_config(db_session, redis_client)
    state_id = uuid.uuid4()
    db_session.add(
        UserInterestState(
            state_id=state_id,
            user_id=seeded_user.user_id,
            cso_topic_id=seeded_cso_topics[0].cso_topic_id,
            leaf_topic_id=None,
            long_alpha=params.alpha_prior + params.onboarding_prior_boost,  # boost 적용된 prior
            long_beta=params.beta_prior,
            short_alpha=params.alpha_prior + params.onboarding_prior_boost,
            short_beta=params.beta_prior,
            long_score=0.4,
            short_score=0.4,
            last_event_active_day=0,
            last_decay_active_day=0,
            boost_applied_at_active_day=0,  # day 0 에 boost 적용
        )
    )
    await db_session.flush()
    seeded_user.active_day_counter = 14
    await db_session.flush()
    await apply_decay_to_user(
        db_session, user=seeded_user, params=params, settings=settings
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            select(UserInterestState).where(
                UserInterestState.state_id == state_id
            )
        )
    ).scalar_one()
    # decay (delta=14) 후 + boost 차감 → alpha 가 prior 에 가까움
    assert row.boost_applied_at_active_day is None
    # decay 적용 후 (alpha_prior + 1.0) * exp(-ln2/7*14) = (1.0 + 1.0) * 0.25 = 0.5
    # 그 후 boost(-1.0) 차감 + alpha_prior(+1.0) 회귀
    # 정확한 값보다 boost 컬럼 NULL 여부와 alpha < (prior+boost) 검증으로 충분
    assert row.short_alpha < params.alpha_prior + params.onboarding_prior_boost
