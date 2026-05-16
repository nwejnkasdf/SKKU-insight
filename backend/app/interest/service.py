"""A6 interest service — ingest_event_atomic + bootstrap + feedback.

핵심:
1) ingest_event_atomic: 동시 race 방어 atomic SQL UPSERT (concurrency.md §4.1).
   - payload_hash idempotency 200/409
   - dwell_tick Redis cap (atomic INCR + TTL)
   - topic distribution P1-4 default
   - propagation feature flag (A7 도입 후 활성)
   - not-interested 하이브리드 (Bayesian 분배 + NotInterestedTopic 최고 confidence 1건)
   - cache invalidate (save/hide/not_interested)
2) bootstrap_interest_state: onboarding 직후 12 cluster + 1-hop child row prefilled
   (alpha_prior+boost), boost_applied_at_active_day=user.active_day_counter.
3) feedback service (save/hide/not_interested) — 명시 액션. SavedDocument/HiddenDocument/
   NotInterestedTopic INSERT + UserEvent + ingest_event_atomic.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import networkx as nx
import redis.asyncio as aioredis
from sqlalchemy import CursorResult, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.contracts import EventType, RedisKey
from app.db.models import (
    BroadInterest,
    HiddenDocument,
    NotInterestedTopic,
    SavedDocument,
    User,
    UserEvent,
    UserInterestState,
)
from app.events.buffer import BufferedEvent
from app.interest.config_loader import (
    EventWeights,
    InterestParams,
    get_event_weights,
    get_interest_params,
)
from app.interest.idempotency import (
    IdempotencyOutcome,
    check_idempotent,
    compute_payload_hash,
    store_idempotent,
)
from app.interest.propagation import compute_ancestor_propagation
from app.interest.topic_distribution import (
    TopicAssignment,
    lookup_document_topics,
    pick_max_confidence,
    resolve_topic_distribution,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    """ingest_event_atomic 응답."""

    event_id: UUID
    accepted: bool
    server_received_at: datetime
    posterior_applied: bool
    duplicate: bool


async def _atomic_upsert_interest_state(
    db: AsyncSession,
    *,
    user_id: UUID,
    cso_topic_id: UUID | None,
    leaf_topic_id: UUID | None,
    weighted: float,
    params: InterestParams,
    active_day: int,
) -> None:
    """atomic INSERT/UPDATE — concurrency.md §4.1 패턴.

    weighted > 0 → short_alpha/long_alpha 가산
    weighted < 0 → short_beta/long_beta 에 |weighted| 가산
    weighted = 0 → no-op (early return)

    partial UNIQUE 3종 (cso_only / leaf_only / pair) 매칭 후 DO UPDATE.
    score 캐시는 새 alpha/beta 로 재계산.
    """
    if weighted == 0.0:
        return
    if weighted > 0:
        da_s, db_s, da_l, db_l = weighted, 0.0, weighted, 0.0
    else:
        absw = -weighted
        da_s, db_s, da_l, db_l = 0.0, absw, 0.0, absw

    # ON CONFLICT target 결정 — partial unique 3종 분기.
    # SQLAlchemy 에서는 untargeted on_conflict_do_nothing 으로 다중 partial unique 모두 매치
    # 하기 어렵 → service 가 SELECT 로 기존 row 존재 여부 먼저 확인 후 UPDATE / INSERT.
    # 단순화: SELECT existing → UPDATE 또는 INSERT (concurrency 는 user-level mutex 또는
    # row-level locking 으로 보장. 일반 endpoint 는 동시 호출 가능 — race 시 INSERT 실패
    # → 재시도 1회).
    #
    # 더 강건한 패턴: pg_insert + on_conflict_do_nothing → row_count==0 면 UPDATE.
    # 본 구현은 사용 패턴이 race-적으므로 단일 atomic SQL UPDATE WHERE 우선 + 없으면 INSERT
    # 패턴. interest-bayesian.md 의사 코드 그대로.
    if cso_topic_id is not None and leaf_topic_id is not None:
        # pair: cso + leaf
        update_sql = text(
            """
            UPDATE user_interest_state
            SET short_alpha = short_alpha + :da_s,
                short_beta  = short_beta  + :db_s,
                long_alpha  = long_alpha  + :da_l,
                long_beta   = long_beta   + :db_l,
                short_score = (short_alpha + :da_s) /
                              NULLIF(short_alpha + :da_s + short_beta + :db_s, 0),
                long_score  = (long_alpha + :da_l) /
                              NULLIF(long_alpha + :da_l + long_beta + :db_l, 0),
                last_event_active_day = :active_day,
                updated_at = NOW()
            WHERE user_id = :user_id
              AND cso_topic_id = :cso_id
              AND leaf_topic_id = :leaf_id
            """
        )
        cond = {
            "user_id": user_id,
            "cso_id": cso_topic_id,
            "leaf_id": leaf_topic_id,
        }
    elif cso_topic_id is not None:
        update_sql = text(
            """
            UPDATE user_interest_state
            SET short_alpha = short_alpha + :da_s,
                short_beta  = short_beta  + :db_s,
                long_alpha  = long_alpha  + :da_l,
                long_beta   = long_beta   + :db_l,
                short_score = (short_alpha + :da_s) /
                              NULLIF(short_alpha + :da_s + short_beta + :db_s, 0),
                long_score  = (long_alpha + :da_l) /
                              NULLIF(long_alpha + :da_l + long_beta + :db_l, 0),
                last_event_active_day = :active_day,
                updated_at = NOW()
            WHERE user_id = :user_id
              AND cso_topic_id = :cso_id
              AND leaf_topic_id IS NULL
            """
        )
        cond = {"user_id": user_id, "cso_id": cso_topic_id}
    elif leaf_topic_id is not None:
        update_sql = text(
            """
            UPDATE user_interest_state
            SET short_alpha = short_alpha + :da_s,
                short_beta  = short_beta  + :db_s,
                long_alpha  = long_alpha  + :da_l,
                long_beta   = long_beta   + :db_l,
                short_score = (short_alpha + :da_s) /
                              NULLIF(short_alpha + :da_s + short_beta + :db_s, 0),
                long_score  = (long_alpha + :da_l) /
                              NULLIF(long_alpha + :da_l + long_beta + :db_l, 0),
                last_event_active_day = :active_day,
                updated_at = NOW()
            WHERE user_id = :user_id
              AND cso_topic_id IS NULL
              AND leaf_topic_id = :leaf_id
            """
        )
        cond = {"user_id": user_id, "leaf_id": leaf_topic_id}
    else:
        # 토픽 둘 다 NULL — CHECK 위반. 호출자가 가드.
        return

    update_result = cast(
        CursorResult[Any],
        await db.execute(
            update_sql,
            {
                **cond,
                "da_s": da_s,
                "db_s": db_s,
                "da_l": da_l,
                "db_l": db_l,
                "active_day": active_day,
            },
        ),
    )
    if update_result.rowcount and update_result.rowcount > 0:
        return

    # INSERT — alpha_prior + 변동분, beta_prior + 변동분.
    initial_short_alpha = params.alpha_prior + da_s
    initial_short_beta = params.beta_prior + db_s
    initial_long_alpha = params.alpha_prior + da_l
    initial_long_beta = params.beta_prior + db_l
    initial_short_score = initial_short_alpha / (
        initial_short_alpha + initial_short_beta
    )
    initial_long_score = initial_long_alpha / (
        initial_long_alpha + initial_long_beta
    )
    insert_stmt = pg_insert(UserInterestState).values(
        state_id=uuid4(),
        user_id=user_id,
        cso_topic_id=cso_topic_id,
        leaf_topic_id=leaf_topic_id,
        long_alpha=initial_long_alpha,
        long_beta=initial_long_beta,
        short_alpha=initial_short_alpha,
        short_beta=initial_short_beta,
        long_score=initial_long_score,
        short_score=initial_short_score,
        last_event_active_day=active_day,
        last_decay_active_day=active_day,
        boost_applied_at_active_day=None,
    )
    # ON CONFLICT DO NOTHING — 동시 INSERT race 시 한 쪽만 성공. 재시도 X (다음 호출이 UPDATE).
    insert_stmt = insert_stmt.on_conflict_do_nothing()
    await db.execute(insert_stmt)


async def _ensure_active_state_for_decay(
    db: AsyncSession,
    *,
    user_id: UUID,
    cso_topic_id: UUID | None,
    leaf_topic_id: UUID | None,
    params: InterestParams,
    active_day: int,
) -> None:
    """UserInterestState row 가 없으면 prior 만으로 생성 (decay 가 동작하도록 시드).

    onboarding boost 외 일반 시점에 호출 — bootstrap 으로 시드 안 된 새 토픽이
    이벤트로 처음 등장할 때.
    """
    initial_short_alpha = params.alpha_prior
    initial_short_beta = params.beta_prior
    initial_long_alpha = params.alpha_prior
    initial_long_beta = params.beta_prior
    initial_score = initial_short_alpha / (initial_short_alpha + initial_short_beta)
    insert_stmt = pg_insert(UserInterestState).values(
        state_id=uuid4(),
        user_id=user_id,
        cso_topic_id=cso_topic_id,
        leaf_topic_id=leaf_topic_id,
        long_alpha=initial_long_alpha,
        long_beta=initial_long_beta,
        short_alpha=initial_short_alpha,
        short_beta=initial_short_beta,
        long_score=initial_score,
        short_score=initial_score,
        last_event_active_day=active_day,
        last_decay_active_day=active_day,
        boost_applied_at_active_day=None,
    )
    insert_stmt = insert_stmt.on_conflict_do_nothing()
    await db.execute(insert_stmt)


async def _record_user_event(
    db: AsyncSession,
    *,
    user_id: UUID,
    event_type: EventType,
    document_id: UUID | None,
    cso_topic_id: UUID | None,
    leaf_topic_id: UUID | None,
    dwell_ms: int | None,
    client_request_id: str,
    payload_hash: str,
    occurred_at: datetime,
) -> UUID:
    """UserEvent INSERT (audit log). cap/view 경로도 호출 (베이지안 skip 이어도 record)."""
    event_id = uuid4()
    db.add(
        UserEvent(
            event_id=event_id,
            user_id=user_id,
            document_id=document_id,
            cso_topic_id=cso_topic_id,
            leaf_topic_id=leaf_topic_id,
            event_type=event_type.value,
            dwell_ms=dwell_ms,
            client_request_id=client_request_id,
            payload_hash=payload_hash,
            occurred_at=occurred_at,
        )
    )
    await db.flush()
    return event_id


async def _apply_bayesian_update(
    db: AsyncSession,
    cso_graph: nx.DiGraph,
    settings: Settings,
    params: InterestParams,
    *,
    user_id: UUID,
    assignments: list[TopicAssignment],
    capped_weight: float,
    active_day: int,
) -> None:
    """assignment list 의 각 (cso, leaf, p_i) 에 대해 capped_weight*p_i 만큼 베이지안 갱신.

    propagation 활성 시 trace path 조상에도 추가 가산 (hop_decay 감쇠).
    """
    for assignment in assignments:
        if assignment.weight == 0.0:
            continue
        w_i = capped_weight * assignment.weight
        await _atomic_upsert_interest_state(
            db,
            user_id=user_id,
            cso_topic_id=assignment.cso_topic_id,
            leaf_topic_id=assignment.leaf_topic_id,
            weighted=w_i,
            params=params,
            active_day=active_day,
        )
        # propagation — leaf 부모 cso_topic 만 (leaf 본인은 위에서 처리). 본 구현에서는
        # cso_topic_id 가 명시된 경우만 ancestor 가산 (leaf 만의 경우는 부모 cso 자체가
        # path 의 한 노드이므로 동일 처리 가능 — DocumentTopic 매핑이 cso 까지 포함되므로).
        if (
            settings.INTEREST_PROPAGATION_ENABLED
            and assignment.cso_topic_id is not None
        ):
            propagations = await compute_ancestor_propagation(
                db,
                cso_graph,
                settings,
                params,
                user_id=user_id,
                leaf_parent_cso_id=assignment.cso_topic_id,
            )
            for prop in propagations:
                await _atomic_upsert_interest_state(
                    db,
                    user_id=user_id,
                    cso_topic_id=prop.cso_topic_id,
                    leaf_topic_id=None,
                    weighted=w_i * prop.attenuation,
                    params=params,
                    active_day=active_day,
                )


async def _check_dwell_tick_cap(
    redis: aioredis.Redis,
    settings: Settings,
    *,
    user_id: UUID,
    document_id: UUID,
) -> bool:
    """dwell_tick 카운터 atomic INCR + TTL. 4 초과 시 False (베이지안 skip 신호)."""
    key = RedisKey.dwell_tick_count(user_id, document_id)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, settings.DWELL_TICK_CAP_TTL_SECONDS)
    return int(count) <= settings.DWELL_TICK_CAP_PER_DOCUMENT


async def _insert_not_interested_topic(
    db: AsyncSession,
    *,
    user_id: UUID,
    cso_topic_id: UUID | None,
    leaf_topic_id: UUID | None,
) -> None:
    """NotInterestedTopic INSERT (ON CONFLICT DO NOTHING — 재거부 무시).

    하이브리드 (정렬 2): 본 함수는 단일 row INSERT. Bayesian 분배는 별도.
    """
    if cso_topic_id is None and leaf_topic_id is None:
        return
    insert_stmt = pg_insert(NotInterestedTopic).values(
        id=uuid4(),
        user_id=user_id,
        cso_topic_id=cso_topic_id,
        leaf_topic_id=leaf_topic_id,
    )
    insert_stmt = insert_stmt.on_conflict_do_nothing()
    await db.execute(insert_stmt)


async def _invalidate_recommendation_cache(
    redis: aioredis.Redis, user_id: UUID
) -> None:
    """save/hide/not_interested 진입 시 추천 캐시 명시 DELETE."""
    await redis.delete(RedisKey.recommendation_cache(user_id))


async def ingest_event_atomic(
    db: AsyncSession,
    redis: aioredis.Redis,
    cso_graph: nx.DiGraph,
    settings: Settings,
    params: InterestParams,
    weights: EventWeights,
    *,
    user: User,
    event_type: EventType,
    document_id: UUID | None,
    cso_topic_id: UUID | None,
    leaf_topic_id: UUID | None,
    dwell_ms: int | None,
    client_request_id: str,
    occurred_at: datetime,
    active_day: int,
    cache_invalidate: bool = False,
) -> IngestResult:
    """행동 로그 1건 atomic 처리. interest-bayesian.md §의사 코드 + 결정 매트릭스.

    절차:
    1) payload_hash 계산 + idempotency check
    2) dwell_tick cap (Redis INCR + TTL)
    3) UserEvent INSERT (audit log) — cap skip 이어도 record
    4) event_type weight = 0 (view) → posterior skip
    5) DocumentTopic 분배 (또는 직접 지정 토픽)
    6) 각 (cso, leaf) 에 대해 atomic UPSERT
    7) propagation feature flag 활성 시 trace path 조상에도 가산
    8) cache_invalidate=True 면 recommendation cache DELETE
    """
    server_received_at = datetime.now(UTC)
    payload_hash = compute_payload_hash(
        event_type=event_type.value,
        document_id=document_id,
        cso_topic_id=cso_topic_id,
        leaf_topic_id=leaf_topic_id,
        dwell_ms=dwell_ms,
        occurred_at=occurred_at,
    )

    # 1) idempotency
    lookup = await check_idempotent(
        db,
        redis,
        user_id=user.user_id,
        client_request_id=client_request_id,
        payload_hash=payload_hash,
    )
    if lookup.outcome == IdempotencyOutcome.DUPLICATE_MATCH:
        assert lookup.existing_event_id is not None
        return IngestResult(
            event_id=lookup.existing_event_id,
            accepted=True,
            server_received_at=lookup.existing_created_at or server_received_at,
            posterior_applied=False,
            duplicate=True,
        )
    if lookup.outcome == IdempotencyOutcome.DUPLICATE_MISMATCH:
        # caller (router) 가 EVENT_DUPLICATE 409 응답으로 변환.
        raise EventDuplicateError(
            existing_event_id=lookup.existing_event_id, user_id=user.user_id
        )

    # 2) dwell_tick cap
    bayesian_skip = False
    if event_type == EventType.DWELL_TICK:
        if document_id is None:
            # dwell_tick 은 document_id 필수. 422 위임.
            raise InvalidEventTargetError(
                "dwell_tick 이벤트는 document_id 필수."
            )
        within_cap = await _check_dwell_tick_cap(
            redis, settings, user_id=user.user_id, document_id=document_id
        )
        if not within_cap:
            bayesian_skip = True

    # 3) UserEvent INSERT (audit)
    event_id = await _record_user_event(
        db,
        user_id=user.user_id,
        event_type=event_type,
        document_id=document_id,
        cso_topic_id=cso_topic_id,
        leaf_topic_id=leaf_topic_id,
        dwell_ms=dwell_ms,
        client_request_id=client_request_id,
        payload_hash=payload_hash,
        occurred_at=occurred_at,
    )
    await store_idempotent(
        redis,
        user_id=user.user_id,
        client_request_id=client_request_id,
        payload_hash=payload_hash,
        event_id=event_id,
        ttl_seconds=settings.EVENT_DUPLICATE_CACHE_TTL_SECONDS,
    )

    # 4) base weight check
    base_weight = weights.lookup(event_type.value)
    posterior_applied = False
    if base_weight != 0.0 and not bayesian_skip:
        capped = max(
            -weights.weight_per_event_max,
            min(base_weight, weights.weight_per_event_max),
        )
        # 5) topic distribution
        assignments = await resolve_topic_distribution(
            db,
            document_id=document_id,
            cso_topic_id=cso_topic_id,
            leaf_topic_id=leaf_topic_id,
        )
        if assignments:
            # 6+7) 베이지안 + propagation
            await _apply_bayesian_update(
                db,
                cso_graph,
                settings,
                params,
                user_id=user.user_id,
                assignments=assignments,
                capped_weight=capped,
                active_day=active_day,
            )
            posterior_applied = True

    # 8) cache invalidate
    if cache_invalidate:
        await _invalidate_recommendation_cache(redis, user.user_id)

    return IngestResult(
        event_id=event_id,
        accepted=True,
        server_received_at=server_received_at,
        posterior_applied=posterior_applied,
        duplicate=False,
    )


# ============================================================
# Onboarding bootstrap — 12 cluster + 1-hop child row prefilled.
# ============================================================


async def bootstrap_interest_state(
    db: AsyncSession,
    cso_graph: nx.DiGraph,
    *,
    user: User,
    cluster_ids: Iterable[UUID],
    active_day: int,
    redis: aioredis.Redis | None = None,
) -> int:
    """onboarding 직후 12 cluster 본인 + 1-hop successor 자식 row prefilled.

    Args:
        cluster_ids: BroadInterest.broad_interest_id 리스트 (사용자 선택).
        active_day: User.active_day_counter 현재 값 (대개 0). boost_applied_at_active_day 에 셋팅.

    Returns: prefilled row 수 (cluster 본인 + 자식 합).

    구현:
    1) cluster_ids → BroadInterest.cso_seed_topic_id 매핑.
    2) cluster 본인: alpha=alpha_prior+boost (1.0), beta=beta_prior.
    3) 각 cluster 의 1-hop child (CSO graph predecessors): alpha=alpha_prior+boost*hop_decay (0.5).
    4) boost_applied_at_active_day = active_day.
    """
    params = (
        await get_interest_params(redis, db) if redis is not None
        else await _load_interest_params_direct(db)
    )
    cluster_id_list = list(cluster_ids)
    if not cluster_id_list:
        return 0
    # 1) BroadInterest → cso_seed_topic_id 매핑
    rows = (
        await db.execute(
            select(
                BroadInterest.broad_interest_id, BroadInterest.cso_seed_topic_id
            ).where(BroadInterest.broad_interest_id.in_(cluster_id_list))
        )
    ).all()
    seed_cso_ids: list[UUID] = [row.cso_seed_topic_id for row in rows]
    inserted = 0

    # 2) cluster 본인 — boost +1.0
    for cso_id in seed_cso_ids:
        inserted += await _insert_boost_row(
            db,
            user_id=user.user_id,
            cso_topic_id=cso_id,
            boost=params.onboarding_prior_boost,
            params=params,
            active_day=active_day,
        )

    # 3) 1-hop child (predecessor 방향 = 자식)
    child_boost = params.onboarding_prior_boost * params.propagation_hop_decay
    for cso_id in seed_cso_ids:
        if cso_id not in cso_graph:
            continue
        for child_id in cso_graph.predecessors(cso_id):
            inserted += await _insert_boost_row(
                db,
                user_id=user.user_id,
                cso_topic_id=cast(UUID, child_id),
                boost=child_boost,
                params=params,
                active_day=active_day,
            )
    return inserted


async def _load_interest_params_direct(db: AsyncSession) -> InterestParams:
    """fallback — Redis 없이 DB 직접 로드 (테스트 환경)."""
    from app.db.models import SystemConfig

    row = (
        await db.execute(
            select(SystemConfig.value).where(SystemConfig.key == "interest_params")
        )
    ).first()
    if row is None:
        raise RuntimeError("system_config interest_params 누락")
    return InterestParams.from_dict(row.value)


async def _insert_boost_row(
    db: AsyncSession,
    *,
    user_id: UUID,
    cso_topic_id: UUID,
    boost: float,
    params: InterestParams,
    active_day: int,
) -> int:
    """boost 적용된 UserInterestState row INSERT (ON CONFLICT DO NOTHING)."""
    alpha = params.alpha_prior + boost
    beta = params.beta_prior
    score = alpha / (alpha + beta)
    stmt = pg_insert(UserInterestState).values(
        state_id=uuid4(),
        user_id=user_id,
        cso_topic_id=cso_topic_id,
        leaf_topic_id=None,
        long_alpha=alpha,
        long_beta=beta,
        short_alpha=alpha,
        short_beta=beta,
        long_score=score,
        short_score=score,
        last_event_active_day=active_day,
        last_decay_active_day=active_day,
        boost_applied_at_active_day=active_day,
    )
    stmt = stmt.on_conflict_do_nothing()
    result = await db.execute(stmt)
    return int(result.rowcount or 0)


# ============================================================
# Feedback service (save / hide / not_interested) — 명시 액션.
# ============================================================


async def save_feedback(
    db: AsyncSession,
    redis: aioredis.Redis,
    cso_graph: nx.DiGraph,
    settings: Settings,
    params: InterestParams,
    weights: EventWeights,
    *,
    user: User,
    document_id: UUID,
    client_request_id: str,
    occurred_at: datetime,
    active_day: int,
) -> tuple[IngestResult, bool]:
    """SavedDocument INSERT + UserEvent + Bayesian + cache invalidate.

    Returns: (IngestResult, already_saved_flag).
    """
    stmt = pg_insert(SavedDocument).values(
        user_id=user.user_id, document_id=document_id
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["user_id", "document_id"]
    )
    result = await db.execute(stmt)
    already_saved = (result.rowcount or 0) == 0
    ingest = await ingest_event_atomic(
        db,
        redis,
        cso_graph,
        settings,
        params,
        weights,
        user=user,
        event_type=EventType.SAVE,
        document_id=document_id,
        cso_topic_id=None,
        leaf_topic_id=None,
        dwell_ms=None,
        client_request_id=client_request_id,
        occurred_at=occurred_at,
        active_day=active_day,
        cache_invalidate=True,
    )
    return ingest, already_saved


async def hide_feedback(
    db: AsyncSession,
    redis: aioredis.Redis,
    cso_graph: nx.DiGraph,
    settings: Settings,
    params: InterestParams,
    weights: EventWeights,
    *,
    user: User,
    document_id: UUID,
    client_request_id: str,
    occurred_at: datetime,
    active_day: int,
) -> IngestResult:
    """HiddenDocument INSERT + UserEvent + Bayesian -3 + cache invalidate."""
    stmt = pg_insert(HiddenDocument).values(
        user_id=user.user_id, document_id=document_id
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["user_id", "document_id"]
    )
    await db.execute(stmt)
    return await ingest_event_atomic(
        db,
        redis,
        cso_graph,
        settings,
        params,
        weights,
        user=user,
        event_type=EventType.HIDE,
        document_id=document_id,
        cso_topic_id=None,
        leaf_topic_id=None,
        dwell_ms=None,
        client_request_id=client_request_id,
        occurred_at=occurred_at,
        active_day=active_day,
        cache_invalidate=True,
    )


async def not_interested_feedback(
    db: AsyncSession,
    redis: aioredis.Redis,
    cso_graph: nx.DiGraph,
    settings: Settings,
    params: InterestParams,
    weights: EventWeights,
    *,
    user: User,
    document_id: UUID | None,
    cso_topic_id: UUID | None,
    leaf_topic_id: UUID | None,
    client_request_id: str,
    occurred_at: datetime,
    active_day: int,
) -> IngestResult:
    """not-interested 하이브리드 (정렬 2).

    Bayesian: ingest_event_atomic 가 P1-4 분배 (document 매핑 토픽 모두 -5*confidence).
    NotInterestedTopic: 최고 confidence 1 row (의도 마킹용).
    - cso/leaf 직접 지정 시: 그 토픽 1 row INSERT.
    - document_id 단독: DocumentTopic 최고 confidence 1 row INSERT.
    """
    # 1) NotInterestedTopic INSERT (의도 마킹)
    target_cso = cso_topic_id
    target_leaf = leaf_topic_id
    if target_cso is None and target_leaf is None and document_id is not None:
        mappings = await lookup_document_topics(db, document_id)
        picked = pick_max_confidence(mappings)
        if picked is not None:
            target_cso = picked.cso_topic_id
            target_leaf = picked.leaf_topic_id
    if target_cso is not None or target_leaf is not None:
        await _insert_not_interested_topic(
            db,
            user_id=user.user_id,
            cso_topic_id=target_cso,
            leaf_topic_id=target_leaf,
        )

    # 2) Bayesian — P1-4 분배 (직접 지정 우선)
    return await ingest_event_atomic(
        db,
        redis,
        cso_graph,
        settings,
        params,
        weights,
        user=user,
        event_type=EventType.NOT_INTERESTED,
        document_id=document_id,
        cso_topic_id=cso_topic_id,
        leaf_topic_id=leaf_topic_id,
        dwell_ms=None,
        client_request_id=client_request_id,
        occurred_at=occurred_at,
        active_day=active_day,
        cache_invalidate=True,
    )


async def list_saved_documents(
    db: AsyncSession, user_id: UUID, *, cursor: datetime | None, limit: int
) -> list[tuple[SavedDocument, datetime]]:
    """SavedDocument saved_at DESC cursor pagination — raw row 반환.

    router 가 Document JOIN + DocumentSummary 변환.
    """
    stmt = select(SavedDocument).where(SavedDocument.user_id == user_id)
    if cursor is not None:
        stmt = stmt.where(SavedDocument.saved_at < cursor)
    stmt = stmt.order_by(SavedDocument.saved_at.desc()).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    return [(row, row.saved_at) for row in rows]


async def delete_saved_document(
    db: AsyncSession, *, user_id: UUID, document_id: UUID
) -> bool:
    """SavedDocument DELETE. 동의 비활성이어도 허용."""
    from sqlalchemy import delete as sa_delete

    stmt = sa_delete(SavedDocument).where(
        SavedDocument.user_id == user_id,
        SavedDocument.document_id == document_id,
    )
    result = await db.execute(stmt)
    return (result.rowcount or 0) > 0


# ============================================================
# Buffer flush callback — service 가 default 등록.
# ============================================================


async def flush_buffered_events(
    user_id: UUID,
    entries: Iterable[BufferedEvent],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    cso_graph: nx.DiGraph,
    redis: aioredis.Redis,
) -> None:
    """EventBuffer flush_callback. 사용자별 entry list 를 일괄 ingest.

    별도 DB session 으로 처리 (request session 외부). flush 실패 entry 는 drop + WARN.
    """
    settings = get_settings()
    async with session_factory() as session:
        try:
            params = await get_interest_params(redis, session)
            weights = await get_event_weights(redis, session)
        except Exception as exc:
            logger.warning(
                "flush_buffered_events: config load failed user_id=%s error=%s",
                user_id,
                exc,
            )
            return
        user = await session.get(User, user_id)
        if user is None:
            logger.warning(
                "flush_buffered_events: user not found user_id=%s drop=%d",
                user_id,
                sum(1 for _ in entries),
            )
            return
        for entry in entries:
            req = entry.request
            try:
                await ingest_event_atomic(
                    session,
                    redis,
                    cso_graph,
                    settings,
                    params,
                    weights,
                    user=user,
                    event_type=req.event_type,
                    document_id=req.document_id,
                    cso_topic_id=req.cso_topic_id,
                    leaf_topic_id=req.leaf_topic_id,
                    dwell_ms=req.dwell_ms,
                    client_request_id=req.client_request_id,
                    occurred_at=req.occurred_at,
                    active_day=entry.active_day_counter,
                    cache_invalidate=False,
                )
            except (EventDuplicateError, InvalidEventTargetError) as exc:
                logger.info(
                    "flush_buffered_events: skip user_id=%s reason=%s",
                    user_id,
                    type(exc).__name__,
                )
                continue
            except Exception as exc:
                logger.warning(
                    "flush_buffered_events: ingest failed user_id=%s error=%s",
                    user_id,
                    exc,
                )
                continue
        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning(
                "flush_buffered_events: commit failed user_id=%s error=%s",
                user_id,
                exc,
            )


class EventDuplicateError(Exception):
    """payload mismatch — caller 가 409 EVENT_DUPLICATE 응답."""

    def __init__(self, *, existing_event_id: UUID | None, user_id: UUID):
        self.existing_event_id = existing_event_id
        self.user_id = user_id
        super().__init__(
            f"event payload mismatch user_id={user_id} existing={existing_event_id}"
        )


class InvalidEventTargetError(Exception):
    """422 EVENT_INVALID_TARGET — dwell_tick document_id 누락 등."""


__all__ = [
    "EventDuplicateError",
    "IngestResult",
    "InvalidEventTargetError",
    "bootstrap_interest_state",
    "delete_saved_document",
    "flush_buffered_events",
    "hide_feedback",
    "ingest_event_atomic",
    "list_saved_documents",
    "not_interested_feedback",
    "save_feedback",
]


async def fetch_user_state(
    db: AsyncSession, user_id: UUID, limit: int = 50
) -> list[UserInterestState]:
    """GET /interest/state 응답용 — long_score DESC, max 50 row.

    bucket 정렬은 router 가 처리 (HIGH→MEDIUM→LOW→NEUTRAL).
    """
    stmt = (
        select(UserInterestState)
        .where(UserInterestState.user_id == user_id)
        .order_by(UserInterestState.long_score.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


async def fetch_max_updated_at(
    db: AsyncSession, user_id: UUID
) -> datetime | None:
    """/interest/state 응답의 updated_at = 사용자 row 중 가장 최신."""
    row = (
        await db.execute(
            select(func.max(UserInterestState.updated_at)).where(
                UserInterestState.user_id == user_id
            )
        )
    ).first()
    return row[0] if row else None
