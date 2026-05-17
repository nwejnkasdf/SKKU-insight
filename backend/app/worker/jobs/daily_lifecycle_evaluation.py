"""daily_lifecycle_evaluation worker — A7 18 UTC trace 강등 + leaf 강등 통합.

cron = INTEREST_DECAY_CRON 와 같은 시각 (0 18 * * *). interest_decay 와 같은 lock 영역
(interest_decay_lock) 을 공유하지 않음 — 본 잡은 trace/leaf 의 강등 (read-mostly UPDATE)
이라 별도 cron entry.

흐름 (사용자별):
1. trace 2단계 retract 평가 (stale 누적 14d 이상 → retract LLM 호출 + path.pop).
2. trace 3단계 archive 평가 (stale 누적 90d 이상 → status='archived').
3. leaf 룰 기반 전이 (active→stale, stale→archived, emerging→archived) 일괄 적용.

A7 결정 #7 (trace 3단계 강등 하이브리드 — 2/3단계 daily cron) + #13 (leaf 라이프사이클
하이브리드 — 강등 daily cron).
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import LeafTopicStatus, RedisKey, TraversalStatus
from app.db.models import (
    DynamicLeafTopic,
    User,
    UserCSOTraversal,
)
from app.db.session import AsyncSessionLocal
from app.leaf_lifecycle.protocol import LifecycleSignals
from app.leaf_lifecycle.rule_evaluator import (
    apply_transitions,
    evaluate_rule_transitions,
)
from app.redis import get_redis
from app.traversal.default import DefaultTraversalEngine

logger = logging.getLogger("daily_lifecycle_evaluation_job")


async def _evaluate_trace_demotion_for_user(
    engine: DefaultTraversalEngine,
    user: User,
) -> tuple[int, int]:
    """trace 2/3단계 강등 (retract + archive). 1단계 stale 마킹은 ingest 직후 이미 됨.

    return: (retracted, archived).
    """
    settings = get_settings()
    # stale trace 들 조회 (1단계 이후 단계 후보).
    stale_traces = list(
        (
            await engine.db.execute(
                select(UserCSOTraversal).where(
                    UserCSOTraversal.user_id == user.user_id,
                    UserCSOTraversal.status == TraversalStatus.STALE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    retracted = 0
    archived = 0
    # (Codex R1 Suggested 1) archive 임계는 stale 진입 후 누적 — stale_idle + archive_after_stale.
    # 결정 #7 표: stale 21일 + 추가 + 누적 archive 90일 의미.
    archive_threshold = (
        settings.TRACE_STALE_IDLE_DAYS + settings.TRACE_ARCHIVE_AFTER_STALE_DAYS
    )
    retract_threshold = (
        settings.TRACE_STALE_IDLE_DAYS + settings.TRACE_RETRACT_AFTER_STALE_DAYS
    )
    for trace in stale_traces:
        idle = (user.active_day_counter or 0) - trace.last_activity_active_day
        if idle >= archive_threshold:
            ok = await engine.archive_if_eligible(trace.trace_id)
            if ok:
                archived += 1
            continue
        if idle >= retract_threshold:
            plan = await engine.evaluate_retract(trace.trace_id)
            if plan is not None:
                retracted += 1
    return retracted, archived


async def _build_leaf_signals(
    db: AsyncSession, user: User
) -> tuple[list[DynamicLeafTopic], LifecycleSignals]:
    """leaf 룰 평가 signals 수집. last_signal_active_day 차이 + 7d window 카운터."""
    leaves = list(
        (
            await db.execute(
                select(DynamicLeafTopic).where(
                    DynamicLeafTopic.user_id == user.user_id,
                    DynamicLeafTopic.status.in_(
                        [
                            LeafTopicStatus.EMERGING.value,
                            LeafTopicStatus.ACTIVE.value,
                            LeafTopicStatus.STALE.value,
                        ]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    signals = LifecycleSignals()
    active_day = user.active_day_counter or 0
    for leaf in leaves:
        signals.idle_active_days[leaf.leaf_topic_id] = max(
            0, active_day - (leaf.last_signal_active_day or 0)
        )
    # 7d window 카운터 — UserEvent click/save 의 leaf 매핑 매칭. 단순화: 7일 active day
    # window 의 events 수를 leaf 별로 정확히 계산하려면 DocumentTopic JOIN 필요.
    # 1차 시연: window 카운터 0 으로 두고 idle 만 사용 (강등 cron 본 잡 목적).
    # 승격/재활성화 (window 기반) 는 ingest 직후 즉시 평가 (별도 service 책임).
    return leaves, signals


async def _evaluate_leaf_demotion_for_user(
    db: AsyncSession,
    user: User,
) -> int:
    """leaf 룰 기반 전이 (강등만 — 승격은 ingest 직후 이미 평가).

    return: applied 전이 수.
    """
    leaves, signals = await _build_leaf_signals(db, user)
    if not leaves:
        return 0
    transitions = evaluate_rule_transitions(leaves, signals)
    # 강등만 적용 (window_promotion, reactivation 은 ingest 직후 즉시 평가 — 본 cron 에서 skip).
    demotions = [
        t for t in transitions
        if t.reason in ("idle_demotion", "stale_archived", "emerging_idle_archived")
    ]
    if not demotions:
        return 0
    return await apply_transitions(db, demotions)


async def _run() -> int:
    """모든 사용자 순회 — daily 18 UTC cron entry."""
    from app.db.engine import get_engine
    from app.llm_provider import get_provider
    from app.topic.graph import build_cso_graph

    db_engine = get_engine()
    graph = await build_cso_graph(db_engine)
    provider = get_provider(get_settings().LLM_PROVIDER)
    redis = get_redis("default")
    settings = get_settings()
    total_retracted = 0
    total_archived = 0
    total_leaf_demoted = 0
    async with AsyncSessionLocal() as db:
        users = list((await db.execute(select(User))).scalars().all())
        for user in users:
            # (Codex R1 Suggested 3) user-mutex (traversal_lock) — 동일 사용자의 trace
            # mutation 이 ingest 또는 trace_merge_job 과 동시 실행 차단.
            mutation_key = RedisKey.traversal_lock(user.user_id)
            acquired = await redis.set(
                mutation_key,
                "1",
                nx=True,
                ex=settings.TRAVERSAL_USER_LOCK_TTL_SECONDS,
            )
            if not acquired:
                logger.info(
                    "daily_lifecycle_evaluation skip user=%s (lock held)",
                    user.user_id,
                )
                continue
            try:
                engine_inst = DefaultTraversalEngine(db, provider, graph)
                r, a = await _evaluate_trace_demotion_for_user(engine_inst, user)
                total_retracted += r
                total_archived += a
                leaf_demoted = await _evaluate_leaf_demotion_for_user(db, user)
                total_leaf_demoted += leaf_demoted
                await db.commit()
                # (Codex R1 Suggested 6) trace/leaf 변경 후 추천 캐시 invalidate.
                if r or a or leaf_demoted:
                    await redis.delete(
                        RedisKey.recommendation_cache(user.user_id)
                    )
            except Exception:
                logger.exception(
                    "daily_lifecycle_evaluation user=%s failed", user.user_id
                )
                await db.rollback()
            finally:
                await redis.delete(mutation_key)
    logger.info(
        "daily_lifecycle_evaluation_job retracted=%d archived=%d leaf_demoted=%d",
        total_retracted,
        total_archived,
        total_leaf_demoted,
    )
    return total_retracted + total_archived + total_leaf_demoted


def daily_lifecycle_evaluation_job() -> None:
    """RQ sync entrypoint."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


__all__ = ["daily_lifecycle_evaluation_job"]
