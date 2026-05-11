"""DynamicLeafTopic read-only endpoint service.

A7 (leaf-lifecycle) 이 데이터 작성 — A3 시점에는 빈 테이블이므로 빈 PagedResponse
또는 404 응답. A7 완료 후 본 service 의 select 가 자연스럽게 동작.

A3 결정 1·8·11:
- list_leaves: 사용자별 격리 + status filter (default ACTIVE) + 빈 응답 OK
- get_leaf_detail: 항상 404 (A7 데이터 부재 시) — A7 완료 후 일반 흐름
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
    ErrorCode,
    LeafTopicStatus,
    PagedResponse,
    PageMeta,
)
from app.db.models import DynamicLeafTopic as DynamicLeafTopicORM
from app.db.models import DynamicLeafTopicCSOTopic
from app.topic.schemas import DynamicLeafTopic as DynamicLeafTopicSchema


def _encode_cursor(created_at: datetime, leaf_id: UUID) -> str:
    """opaque base64 cursor. cursor = base64({created_at, leaf_id})."""
    payload = json.dumps(
        {"ts": created_at.isoformat(), "id": str(leaf_id)}
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """opaque cursor → (created_at, leaf_id). 잘못된 cursor 는 400 raise.

    Codex 감사 B-2 fix: TypeError (data 가 list 등 dict 아닌 경우) + 잘못된 type
    필드도 400 wrap. 기존 KeyError/ValueError 외에 TypeError 추가.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError(f"cursor payload must be dict, got {type(data).__name__}")
        ts_val = data["ts"]
        id_val = data["id"]
        if not isinstance(ts_val, str) or not isinstance(id_val, str):
            raise TypeError("cursor fields ts/id must be str")
        return datetime.fromisoformat(ts_val), UUID(id_val)
    except (binascii.Error, TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": f"잘못된 cursor: {e}",
            },
        ) from e


async def list_leaves(
    db: AsyncSession,
    user_id: UUID,
    status_filter: LeafTopicStatus | None,
    cursor: str | None,
    limit: int,
) -> PagedResponse[DynamicLeafTopicSchema]:
    """사용자별 leaf 목록 (status filter + cursor pagination).

    A3 시점에는 dynamic_leaf_topic 비어 있어 items=[] 반환. A7 가 데이터 채우면
    자연스럽게 row 노출.
    """
    stmt = select(DynamicLeafTopicORM).where(
        DynamicLeafTopicORM.user_id == user_id
    )
    if status_filter is not None:
        stmt = stmt.where(DynamicLeafTopicORM.status == status_filter.value)
    if cursor:
        ts, leaf_id = _decode_cursor(cursor)
        # created_at DESC 정렬 → cursor 보다 더 오래된 항목
        stmt = stmt.where(
            (DynamicLeafTopicORM.created_at < ts)
            | (
                (DynamicLeafTopicORM.created_at == ts)
                & (DynamicLeafTopicORM.leaf_topic_id < leaf_id)
            )
        )
    stmt = stmt.order_by(
        desc(DynamicLeafTopicORM.created_at),
        desc(DynamicLeafTopicORM.leaf_topic_id),
    ).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    page = rows[:limit]

    # 각 leaf 의 cso_topic_ids 매핑 조회
    leaf_ids = [r.leaf_topic_id for r in page]
    cso_map: dict[UUID, list[UUID]] = {lid: [] for lid in leaf_ids}
    if leaf_ids:
        map_stmt = select(
            DynamicLeafTopicCSOTopic.leaf_topic_id,
            DynamicLeafTopicCSOTopic.cso_topic_id,
        ).where(DynamicLeafTopicCSOTopic.leaf_topic_id.in_(leaf_ids))
        map_rows = await db.execute(map_stmt)
        for m in map_rows:
            cso_map[m.leaf_topic_id].append(m.cso_topic_id)

    items = [
        DynamicLeafTopicSchema(
            leaf_topic_id=r.leaf_topic_id,
            label=r.label,
            confidence=r.confidence,
            status=LeafTopicStatus(r.status),
            created_at=r.created_at,
            cso_topic_ids=cso_map.get(r.leaf_topic_id, []),
            merged_into_leaf_topic_id=r.merged_into_leaf_topic_id,
        )
        for r in page
    ]
    next_cursor = (
        _encode_cursor(page[-1].created_at, page[-1].leaf_topic_id)
        if has_more and page
        else None
    )
    return PagedResponse[DynamicLeafTopicSchema](
        items=items,
        meta=PageMeta(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(items),
        ),
    )


async def get_leaf_detail(
    db: AsyncSession, user_id: UUID, leaf_topic_id: UUID
) -> DynamicLeafTopicSchema:
    """단일 leaf 상세. 본인 소유 + 존재 검증. 부재·타인 row 모두 404 (결정 11).

    enumeration attack 차단: 존재하지 않거나 타인 leaf 둘 다 동일 404 응답.
    """
    stmt = select(DynamicLeafTopicORM).where(
        DynamicLeafTopicORM.leaf_topic_id == leaf_topic_id,
        DynamicLeafTopicORM.user_id == user_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": ErrorCode.TOPIC_NOT_FOUND.value,
                "message": "리프 토픽을 찾을 수 없습니다.",
            },
        )

    map_stmt = select(DynamicLeafTopicCSOTopic.cso_topic_id).where(
        DynamicLeafTopicCSOTopic.leaf_topic_id == leaf_topic_id
    )
    cso_ids = list((await db.execute(map_stmt)).scalars())

    return DynamicLeafTopicSchema(
        leaf_topic_id=row.leaf_topic_id,
        label=row.label,
        confidence=row.confidence,
        status=LeafTopicStatus(row.status),
        created_at=row.created_at,
        cso_topic_ids=cso_ids,
        merged_into_leaf_topic_id=row.merged_into_leaf_topic_id,
    )


__all__ = ["get_leaf_detail", "list_leaves"]
