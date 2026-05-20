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
import uuid

from sqlalchemy import select, text
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


# (Codex R2-RG-3 fix) lock release 가 자기 토큰 일치 시만 DEL — TTL 만료 후 다른 worker
# 의 lock 을 잘못 해제하는 race 차단.
_RELEASE_LOCK_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
)


async def _evaluate_trace_expansion_for_user(
    db: AsyncSession,
    engine: DefaultTraversalEngine,
    user: User,
) -> tuple[int, int]:
    """active trace path 끝 자식 cso 중 user_event 임계 통과 자식들을 가져와
    1개면 `evaluate_extend`, ≥2개면 `evaluate_split` 호출 — daily 18 UTC.

    A7 결정 #14 (extend 임계 `TRACE_EXTEND_MIN_INTERACTIONS`) + #20 (split: 두 자식
    동시 부상). P1-12 fix (C-45 라운드, 2026-05-20): A7 PR-stack 누락분 — extend /
    split caller 가 production code 어디서도 호출 안 되던 결함을 본 helper 가 채움.

    Window: active day delta (벽시계 아님 — SRS 시간모델 SOR 정합).
    Event type: 모든 type 카운트 (사용자 결정 — view/click/save/hide/not_interested 다).
    NULL active_day_at_event row 는 window 밖으로 취급 (0009 이전 row).

    return: (extended, split) — 각각 extend / split 적용된 trace 수.
    """
    settings = get_settings()
    threshold = settings.TRACE_EXTEND_MIN_INTERACTIONS
    current_ad = user.active_day_counter or 0
    active_traces = list(
        (
            await db.execute(
                select(UserCSOTraversal).where(
                    UserCSOTraversal.user_id == user.user_id,
                    UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    extended = 0
    split = 0
    for trace in active_traces:
        path = list(trace.path or [])
        if not path:
            continue
        tail_cso = path[-1]
        # 임계 통과 자식들 (count DESC) — split 가능성 위해 top 2 까지.
        rows = (
            await db.execute(
                text(
                    """
                    SELECT ctp.cso_topic_id AS child_cso,
                           COUNT(DISTINCT ue.event_id) AS cnt
                    FROM cso_topic_parent ctp
                    LEFT JOIN document_topic dt ON dt.cso_topic_id = ctp.cso_topic_id
                    LEFT JOIN user_event ue ON ue.document_id = dt.document_id
                      AND ue.user_id = :user_id
                      AND ue.active_day_at_event IS NOT NULL
                      AND (:current_ad - ue.active_day_at_event) <= :window_days
                    WHERE ctp.parent_cso_topic_id = :tail_cso
                    GROUP BY ctp.cso_topic_id
                    HAVING COUNT(DISTINCT ue.event_id) >= :threshold
                    ORDER BY COUNT(DISTINCT ue.event_id) DESC
                    LIMIT 2
                    """
                ),
                {
                    "user_id": user.user_id,
                    "tail_cso": tail_cso,
                    "threshold": threshold,
                    "current_ad": current_ad,
                    "window_days": 7,
                },
            )
        ).all()
        candidates = [r.child_cso for r in rows if r.child_cso not in path]
        if not candidates:
            continue
        if len(candidates) == 1:
            try:
                ok = await engine.evaluate_extend(trace.trace_id, candidates[0])
            except Exception:
                logger.exception(
                    "evaluate_extend failed user=%s trace=%s child=%s",
                    user.user_id,
                    trace.trace_id,
                    candidates[0],
                )
                continue
            if ok:
                extended += 1
                logger.info(
                    "trace extended user=%s trace=%s tail=%s child=%s",
                    user.user_id,
                    trace.trace_id,
                    tail_cso,
                    candidates[0],
                )
        else:
            # ≥2 자식 동시 부상 — split (T 가 top1 으로 extend, T' 가 top2 로 fork).
            try:
                plan = await engine.evaluate_split(
                    trace.trace_id, candidates[:2]
                )
            except Exception:
                logger.exception(
                    "evaluate_split failed user=%s trace=%s children=%s",
                    user.user_id,
                    trace.trace_id,
                    candidates[:2],
                )
                continue
            if plan is not None:
                split += 1
                logger.info(
                    "trace split user=%s trace=%s tail=%s children=%s",
                    user.user_id,
                    trace.trace_id,
                    tail_cso,
                    candidates[:2],
                )
    return extended, split


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
            # (Codex R2-RG-2 fix) path 길이 1 인 trace 는 retract 무의미 — archive 로 직접 전이.
            # evaluate_retract 가 None 반환하면 caller (retracted += 1 카운트 누락) +
            # cache invalidate skip 됨. 본 fix 가 path=1 시점 명시 archive count.
            if len(list(trace.path or [])) <= 1:
                ok = await engine.archive_if_eligible(trace.trace_id)
                if ok:
                    archived += 1
                continue
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
    total_extended = 0
    total_split = 0
    async with AsyncSessionLocal() as db:
        users = list((await db.execute(select(User))).scalars().all())
        for user in users:
            # (Codex R1 Suggested 3 + R2-RG-3 + R3-RG-S1) user-mutex (traversal_lock) —
            # 동일 사용자의 trace mutation 이 ingest 또는 trace_merge_job 과 동시 실행 차단.
            # lock value 가 고유 token (uuid4) — release 시 자기 토큰만 DEL (Lua atomic).
            # (R3-RG-S1) daily lifecycle 의 retract LLM 호출 + leaf 다중 UPDATE 가 ingest
            # 의 10s 보다 길 수 있음 — max(traversal_ttl, trace_merge_ttl) 사용.
            mutation_key = RedisKey.traversal_lock(user.user_id)
            lock_token = str(uuid.uuid4())
            lock_ttl = max(
                settings.TRAVERSAL_USER_LOCK_TTL_SECONDS,
                settings.TRACE_MERGE_LOCK_TTL_SECONDS,
            )
            acquired = await redis.set(
                mutation_key,
                lock_token,
                nx=True,
                ex=lock_ttl,
            )
            if not acquired:
                logger.info(
                    "daily_lifecycle_evaluation skip user=%s (lock held)",
                    user.user_id,
                )
                continue
            try:
                engine_inst = DefaultTraversalEngine(db, provider, graph)
                # P1-12 (C-45, 2026-05-20): expansion 평가 먼저 — active trace 가
                # path 늘어난 후 demotion 평가가 새 tail 기준으로 동작.
                ext, spl = await _evaluate_trace_expansion_for_user(
                    db, engine_inst, user
                )
                total_extended += ext
                total_split += spl
                r, a = await _evaluate_trace_demotion_for_user(engine_inst, user)
                total_retracted += r
                total_archived += a
                leaf_demoted = await _evaluate_leaf_demotion_for_user(db, user)
                total_leaf_demoted += leaf_demoted
                await db.commit()
                # (Codex R1 Suggested 6) trace/leaf 변경 후 추천 캐시 invalidate.
                if ext or spl or r or a or leaf_demoted:
                    await redis.delete(
                        RedisKey.recommendation_cache(user.user_id)
                    )
            except Exception:
                logger.exception(
                    "daily_lifecycle_evaluation user=%s failed", user.user_id
                )
                await db.rollback()
            finally:
                # R2-RG-3 fix: Lua atomic — 자기 token 일치 시만 DEL.
                try:
                    await redis.eval(  # type: ignore[misc]
                        _RELEASE_LOCK_LUA, 1, mutation_key, lock_token
                    )
                except Exception:
                    logger.warning(
                        "daily_lifecycle_evaluation lock release race user=%s",
                        user.user_id,
                    )
    logger.info(
        "daily_lifecycle_evaluation_job extended=%d split=%d retracted=%d archived=%d leaf_demoted=%d",
        total_extended,
        total_split,
        total_retracted,
        total_archived,
        total_leaf_demoted,
    )
    return (
        total_extended
        + total_split
        + total_retracted
        + total_archived
        + total_leaf_demoted
    )


def daily_lifecycle_evaluation_job() -> None:
    """RQ sync entrypoint."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


__all__ = ["daily_lifecycle_evaluation_job"]
