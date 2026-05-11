"""UserCSOTraversal read-only endpoint service.

A7 가 traversal trace 본문 (extend/retract/split/archive) 작성. A3 시점에는 빈
테이블 → list 빈 응답, detail 404.

NFR-04 마스킹 (A3 결정 7):
- TraversalTraceSummary: score_tail 미노출 (스키마에 필드 없음)
- TraversalTraceDetail: score_tail = None (일반 사용자 응답)
관리자 endpoint 별도 (A10).

A3 결정 11: GET /topics/traces/{id} 404 일관 응답 (enumeration attack 차단).
docs/api/topics.md: ?status 미제공 default = active+stale 만 (archived 제외).
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import (
    CSOTopicSummary,
    ErrorCode,
    LeafTopicStatus,
    PagedResponse,
    PageMeta,
    TraversalStatus,
)
from app.db.models import CSOTopic, DynamicLeafTopicCSOTopic, UserCSOTraversal
from app.db.models import DynamicLeafTopic as DynamicLeafTopicORM
from app.topic.schemas import DynamicLeafTopic as DynamicLeafTopicSchema
from app.topic.schemas import TraversalTraceDetail, TraversalTraceSummary


def _encode_cursor(updated_at: datetime, trace_id: UUID) -> str:
    payload = json.dumps(
        {"ts": updated_at.isoformat(), "id": str(trace_id)}
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded)
        data = json.loads(raw)
        return datetime.fromisoformat(data["ts"]), UUID(data["id"])
    except (binascii.Error, ValueError, KeyError, json.JSONDecodeError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": f"잘못된 cursor: {e}",
            },
        ) from e


async def _lookup_labels(
    db: AsyncSession, cso_topic_ids: list[UUID]
) -> dict[UUID, str]:
    """UUID list → {uuid: label}. trace.path UUID 를 라벨로 변환."""
    if not cso_topic_ids:
        return {}
    stmt = select(CSOTopic.cso_topic_id, CSOTopic.label).where(
        CSOTopic.cso_topic_id.in_(cso_topic_ids)
    )
    rows = await db.execute(stmt)
    return {r.cso_topic_id: r.label for r in rows}


async def list_traces(
    db: AsyncSession,
    user_id: UUID,
    status_filter: TraversalStatus | None,
    cursor: str | None,
    limit: int,
) -> PagedResponse[TraversalTraceSummary]:
    """사용자별 trace 목록.

    ?status 미제공 → active + stale (archived 제외, docs/api/topics.md).
    ?status=archived 명시 → archived 만.
    """
    stmt = select(UserCSOTraversal).where(UserCSOTraversal.user_id == user_id)
    if status_filter is None:
        # default = active + stale
        stmt = stmt.where(
            UserCSOTraversal.status.in_(
                [TraversalStatus.ACTIVE.value, TraversalStatus.STALE.value]
            )
        )
    else:
        stmt = stmt.where(UserCSOTraversal.status == status_filter.value)
    if cursor:
        ts, tid = _decode_cursor(cursor)
        stmt = stmt.where(
            (UserCSOTraversal.updated_at < ts)
            | (
                (UserCSOTraversal.updated_at == ts)
                & (UserCSOTraversal.trace_id < tid)
            )
        )
    stmt = stmt.order_by(
        desc(UserCSOTraversal.updated_at),
        desc(UserCSOTraversal.trace_id),
    ).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    page = rows[:limit]

    # path UUID → label 일괄 조회
    all_path_ids: set[UUID] = set()
    for r in page:
        all_path_ids.update(r.path)
    labels_map = await _lookup_labels(db, list(all_path_ids))

    # leaf_count: path 위 cso_topic_id 에 매핑된 active leaf 수 (사용자 격리)
    leaf_counts: dict[UUID, int] = {r.trace_id: 0 for r in page}
    if page:
        # path 위 CSO 노드를 매핑한 leaf 중 status=active 한 것 사용자 격리
        for r in page:
            if not r.path:
                continue
            count_stmt = (
                select(DynamicLeafTopicORM.leaf_topic_id)
                .join(
                    DynamicLeafTopicCSOTopic,
                    DynamicLeafTopicCSOTopic.leaf_topic_id
                    == DynamicLeafTopicORM.leaf_topic_id,
                )
                .where(
                    DynamicLeafTopicORM.user_id == user_id,
                    DynamicLeafTopicORM.status == LeafTopicStatus.ACTIVE.value,
                    DynamicLeafTopicCSOTopic.cso_topic_id.in_(r.path),
                )
                .distinct()
            )
            count_rows = (await db.execute(count_stmt)).scalars().all()
            leaf_counts[r.trace_id] = len(count_rows)

    items = [
        TraversalTraceSummary(
            trace_id=r.trace_id,
            path_labels=[
                labels_map.get(pid, str(pid)) for pid in r.path
            ],
            status=TraversalStatus(r.status),
            started_active_day=r.started_active_day,
            last_activity_active_day=r.last_activity_active_day,
            leaf_count=leaf_counts.get(r.trace_id, 0),
        )
        for r in page
    ]
    next_cursor = (
        _encode_cursor(page[-1].updated_at, page[-1].trace_id)
        if has_more and page
        else None
    )
    return PagedResponse[TraversalTraceSummary](
        items=items,
        meta=PageMeta(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(items),
        ),
    )


async def get_trace_detail(
    db: AsyncSession, user_id: UUID, trace_id: UUID
) -> TraversalTraceDetail:
    """trace 상세. score_tail NFR-04 마스킹 (None) — 결정 7.

    부재·타인 row 모두 404 (결정 11, enumeration 차단).
    """
    stmt = select(UserCSOTraversal).where(
        UserCSOTraversal.trace_id == trace_id,
        UserCSOTraversal.user_id == user_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": ErrorCode.TOPIC_NOT_FOUND.value,
                "message": "trace 를 찾을 수 없습니다.",
            },
        )

    labels_map = await _lookup_labels(db, list(row.path))
    path = [
        CSOTopicSummary(
            cso_topic_id=pid, label=labels_map.get(pid, str(pid))
        )
        for pid in row.path
    ]

    # path 위 노드에 매핑된 사용자 소유 active leaf
    leaves: list[DynamicLeafTopicSchema] = []
    if row.path:
        leaf_stmt = (
            select(DynamicLeafTopicORM)
            .join(
                DynamicLeafTopicCSOTopic,
                DynamicLeafTopicCSOTopic.leaf_topic_id
                == DynamicLeafTopicORM.leaf_topic_id,
            )
            .where(
                DynamicLeafTopicORM.user_id == user_id,
                DynamicLeafTopicCSOTopic.cso_topic_id.in_(row.path),
            )
            .distinct()
        )
        leaf_rows = (await db.execute(leaf_stmt)).scalars().all()
        # leaf 각각의 cso_topic_ids 매핑 조회
        leaf_ids = [lf.leaf_topic_id for lf in leaf_rows]
        cso_map: dict[UUID, list[UUID]] = {lid: [] for lid in leaf_ids}
        if leaf_ids:
            map_stmt = select(
                DynamicLeafTopicCSOTopic.leaf_topic_id,
                DynamicLeafTopicCSOTopic.cso_topic_id,
            ).where(DynamicLeafTopicCSOTopic.leaf_topic_id.in_(leaf_ids))
            for m in await db.execute(map_stmt):
                cso_map[m.leaf_topic_id].append(m.cso_topic_id)
        leaves = [
            DynamicLeafTopicSchema(
                leaf_topic_id=lf.leaf_topic_id,
                label=lf.label,
                confidence=lf.confidence,
                status=LeafTopicStatus(lf.status),
                created_at=lf.created_at,
                cso_topic_ids=cso_map.get(lf.leaf_topic_id, []),
                merged_into_leaf_topic_id=lf.merged_into_leaf_topic_id,
            )
            for lf in leaf_rows
        ]

    return TraversalTraceDetail(
        trace_id=row.trace_id,
        path=path,
        status=TraversalStatus(row.status),
        leaves=leaves,
        started_active_day=row.started_active_day,
        last_activity_active_day=row.last_activity_active_day,
        # NFR-04: 일반 사용자 응답에서 score_tail 마스킹 (None). 관리자 endpoint 별도 (A10).
        score_tail=None,
    )


__all__ = ["get_trace_detail", "list_traces"]
