"""admin 인사이트 조회 — SUPER 전용 raw 노출 (C-61).

docs/decisions.md §24, docs/api/admin.md §인사이트.

5 service:
- get_admin_me              — AdminMeResponse 변환
- get_admin_traces          — user_cso_traversal 전체 + path label + leaf_count
- get_admin_leaves          — dynamic_leaf_topic 전체 + cso 매핑
- get_admin_recommendations — recommendation 최근 N개 + document.title
- get_admin_interest_state  — user_interest_state 전체 + bucket

NFR-04 마스킹은 일반 사용자 응답만 적용. 본 모듈은 admin 노출용이라 score / long_score /
short_score / origin_ref 등 raw 그대로 직렬화.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import AdminRole, LeafTopicStatus, SlotType, TraversalStatus
from app.db.models import (
    AdminUser,
    CSOTopic,
    Document,
    DynamicLeafTopic,
    DynamicLeafTopicCSOTopic,
    Recommendation,
    UserCSOTraversal,
    UserInterestState,
)
from app.interest.bucket import bucket_for
from app.interest.config_loader import get_interest_params

from .schemas import (
    AdminInterestTopicView,
    AdminLeafView,
    AdminMeResponse,
    AdminRecommendationView,
    AdminTraceView,
    AdminUserInterestState,
)


def get_admin_me(admin: AdminUser) -> AdminMeResponse:
    """AdminUser ORM → AdminMeResponse. router 에서 호출."""
    return AdminMeResponse(
        admin_id=admin.admin_id,
        email=admin.email,
        role=AdminRole(admin.role),
        status=admin.status,  # type: ignore[arg-type]
        last_login_at=admin.last_login_at,
    )


async def get_admin_traces(
    db: AsyncSession, user_id: UUID
) -> list[AdminTraceView]:
    """user_cso_traversal 전체 + path UUID → label + 산하 emerging/active leaf 수."""
    traces = (
        (
            await db.execute(
                select(UserCSOTraversal)
                .where(UserCSOTraversal.user_id == user_id)
                .order_by(
                    desc(UserCSOTraversal.last_activity_active_day),
                    desc(UserCSOTraversal.created_at),
                )
            )
        )
        .scalars()
        .all()
    )
    if not traces:
        return []

    # path 위 cso label 한 번에 lookup.
    all_cso_ids: set[UUID] = {cso_id for t in traces for cso_id in t.path}
    label_map: dict[UUID, str] = {}
    if all_cso_ids:
        rows = (
            await db.execute(
                select(CSOTopic.cso_topic_id, CSOTopic.label).where(
                    CSOTopic.cso_topic_id.in_(list(all_cso_ids))
                )
            )
        ).all()
        label_map = {row.cso_topic_id: row.label for row in rows}

    # path 위 cso 별 산하 emerging/active leaf 수 — leaf ↔ cso 매핑 한 번에.
    leaf_per_cso: dict[UUID, set[UUID]] = {}
    if all_cso_ids:
        mapping_rows = (
            await db.execute(
                select(
                    DynamicLeafTopicCSOTopic.cso_topic_id,
                    DynamicLeafTopicCSOTopic.leaf_topic_id,
                )
                .join(
                    DynamicLeafTopic,
                    DynamicLeafTopic.leaf_topic_id
                    == DynamicLeafTopicCSOTopic.leaf_topic_id,
                )
                .where(
                    DynamicLeafTopic.user_id == user_id,
                    DynamicLeafTopic.status.in_(
                        [
                            LeafTopicStatus.EMERGING.value,
                            LeafTopicStatus.ACTIVE.value,
                        ]
                    ),
                    DynamicLeafTopicCSOTopic.cso_topic_id.in_(list(all_cso_ids)),
                )
            )
        ).all()
        for row in mapping_rows:
            leaf_per_cso.setdefault(row.cso_topic_id, set()).add(row.leaf_topic_id)

    views: list[AdminTraceView] = []
    for trace in traces:
        path_labels = [label_map.get(cid, "?") for cid in trace.path]
        leaf_ids: set[UUID] = set()
        for cid in trace.path:
            leaf_ids |= leaf_per_cso.get(cid, set())
        views.append(
            AdminTraceView(
                trace_id=trace.trace_id,
                path=list(trace.path),
                path_labels=path_labels,
                status=TraversalStatus(trace.status),
                started_active_day=trace.started_active_day,
                last_activity_active_day=trace.last_activity_active_day,
                archived_at_active_day=trace.archived_at_active_day,
                score_tail=trace.score_tail,
                merged_into_trace_id=trace.merged_into_trace_id,
                leaf_count=len(leaf_ids),
                created_at=trace.created_at,
                updated_at=trace.updated_at,
            )
        )
    return views


async def get_admin_leaves(
    db: AsyncSession, user_id: UUID
) -> list[AdminLeafView]:
    """dynamic_leaf_topic 전체 + cso 매핑 (label join)."""
    leaves = (
        (
            await db.execute(
                select(DynamicLeafTopic)
                .where(DynamicLeafTopic.user_id == user_id)
                .order_by(
                    desc(DynamicLeafTopic.last_signal_active_day),
                    desc(DynamicLeafTopic.created_at),
                )
            )
        )
        .scalars()
        .all()
    )
    if not leaves:
        return []

    leaf_ids = [leaf.leaf_topic_id for leaf in leaves]
    mapping_rows = (
        await db.execute(
            select(
                DynamicLeafTopicCSOTopic.leaf_topic_id,
                DynamicLeafTopicCSOTopic.cso_topic_id,
                CSOTopic.label,
            )
            .join(
                CSOTopic,
                CSOTopic.cso_topic_id == DynamicLeafTopicCSOTopic.cso_topic_id,
            )
            .where(DynamicLeafTopicCSOTopic.leaf_topic_id.in_(leaf_ids))
        )
    ).all()
    per_leaf: dict[UUID, list[tuple[UUID, str]]] = {}
    for row in mapping_rows:
        per_leaf.setdefault(row.leaf_topic_id, []).append(
            (row.cso_topic_id, row.label)
        )

    views: list[AdminLeafView] = []
    for leaf in leaves:
        mappings = per_leaf.get(leaf.leaf_topic_id, [])
        views.append(
            AdminLeafView(
                leaf_topic_id=leaf.leaf_topic_id,
                label=leaf.label,
                label_en=leaf.label_en,
                confidence=leaf.confidence,
                status=LeafTopicStatus(leaf.status),
                created_active_day=leaf.created_active_day,
                last_signal_active_day=leaf.last_signal_active_day,
                merged_into_leaf_topic_id=leaf.merged_into_leaf_topic_id,
                cso_mappings=[m[0] for m in mappings],
                cso_mapping_labels=[m[1] for m in mappings],
                created_at=leaf.created_at,
            )
        )
    return views


async def get_admin_recommendations(
    db: AsyncSession, user_id: UUID, limit: int = 50
) -> list[AdminRecommendationView]:
    """recommendation 최근 N개 + document.title join."""
    rows = (
        await db.execute(
            select(
                Recommendation.recommendation_id,
                Recommendation.document_id,
                Document.title,
                Recommendation.slot_type,
                Recommendation.score,
                Recommendation.reason,
                Recommendation.origin_type,
                Recommendation.origin_ref,
                Recommendation.created_at,
            )
            .join(Document, Document.document_id == Recommendation.document_id)
            .where(Recommendation.user_id == user_id)
            .order_by(desc(Recommendation.created_at))
            .limit(limit)
        )
    ).all()
    return [
        AdminRecommendationView(
            recommendation_id=row.recommendation_id,
            document_id=row.document_id,
            document_title=row.title,
            slot_type=SlotType(row.slot_type),
            score=row.score,
            reason=row.reason,
            origin_type=row.origin_type,
            origin_ref=row.origin_ref,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def get_admin_interest_state(
    db: AsyncSession,
    redis: aioredis.Redis,
    user_id: UUID,
) -> AdminUserInterestState:
    """user_interest_state 전체 + cso/leaf label + bucket (raw score 포함)."""
    rows = (
        (
            await db.execute(
                select(UserInterestState).where(UserInterestState.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )

    cso_ids = {r.cso_topic_id for r in rows if r.cso_topic_id is not None}
    leaf_ids = {r.leaf_topic_id for r in rows if r.leaf_topic_id is not None}
    cso_labels: dict[UUID, str] = {}
    leaf_labels: dict[UUID, str] = {}
    if cso_ids:
        cso_rows = (
            await db.execute(
                select(CSOTopic.cso_topic_id, CSOTopic.label).where(
                    CSOTopic.cso_topic_id.in_(list(cso_ids))
                )
            )
        ).all()
        cso_labels = {r.cso_topic_id: r.label for r in cso_rows}
    if leaf_ids:
        leaf_rows = (
            await db.execute(
                select(
                    DynamicLeafTopic.leaf_topic_id, DynamicLeafTopic.label
                ).where(DynamicLeafTopic.leaf_topic_id.in_(list(leaf_ids)))
            )
        ).all()
        leaf_labels = {r.leaf_topic_id: r.label for r in leaf_rows}

    params = await get_interest_params(redis, db)
    topics: list[AdminInterestTopicView] = []
    latest_updated: datetime | None = None
    for r in rows:
        if r.cso_topic_id is not None:
            label = cso_labels.get(r.cso_topic_id, "?")
        elif r.leaf_topic_id is not None:
            label = leaf_labels.get(r.leaf_topic_id, "?")
        else:
            label = "?"
        topics.append(
            AdminInterestTopicView(
                cso_topic_id=r.cso_topic_id,
                leaf_topic_id=r.leaf_topic_id,
                label=label,
                long_score=r.long_score,
                short_score=r.short_score,
                bucket=bucket_for(r.long_score, r.short_score, params),
            )
        )
        if latest_updated is None or r.updated_at > latest_updated:
            latest_updated = r.updated_at

    return AdminUserInterestState(
        user_id=user_id,
        topics=topics,
        updated_at=latest_updated if latest_updated is not None else datetime.now(UTC),
    )


__all__ = [
    "get_admin_interest_state",
    "get_admin_leaves",
    "get_admin_me",
    "get_admin_recommendations",
    "get_admin_traces",
]
