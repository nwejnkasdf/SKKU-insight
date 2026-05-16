"""GET /topics/{topic_id}/documents 본문 — v13 라운드 A4 cross-cutting.

A3 가 NotImplementedError 로 남긴 endpoint 를 A4 가 채움 (Document/DocumentTopic 영속 후).
A8 가 후속 PR 에서 NotInterestedTopic / HiddenDocument / ClickbaitResult 필터 추가
(현재는 PLACEHOLDER 주석만 — 미구현 외부 의존이 들어오면 endpoint 가 깨지지 않게).

비즈니스 룰:
- topic_type 판정:
  - cso_topic_id=topic_id 있으면 'cso'
  - dynamic_leaf_topic.leaf_topic_id=topic_id AND user_id=current_user → 'leaf'
  - 둘 다 아니면 404 topic.not_found (enumeration 차단, trace_service 패턴)
- 사용자 격리: leaf 응답은 본인 row 만 (다른 사용자 leaf 의 doc 누수 차단)
- ORDER BY coalesce(published_at, created_at) DESC, document_id DESC (S-04 fix)
- cursor: base64(json({ts, id})) — ts = coalesce(published_at, created_at) (응답·SQL 일관)
- since 필터: coalesce(published_at, created_at) >= since (NULL row 도 cutoff 정합 비교)
- A8 filter PLACEHOLDER (NotInterestedTopic / HiddenDocument / ClickbaitResult)

(v13 round 2 Codex S-04, 2026-05-16) cursor 가 created_at fallback 으로 encode 됐는데
WHERE 절은 published_at 만 비교 → published_at NULL row 가 페이지네이션에서 누락.
fix: ORDER BY / cursor / since 모두 coalesce(published_at, created_at) 통일.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import (
    CSOTopicSummary,
    DocumentSummary,
    ErrorCode,
    PageMeta,
    SourceType,
)
from app.db.models import (
    CSOTopic,
    Document,
    DocumentTopic,
    DynamicLeafTopic,
    Source,
)
from app.topic.schemas import TopicDocumentsResponse


def _encode_cursor(ts: datetime, doc_id: UUID) -> str:
    payload = json.dumps({"ts": ts.isoformat(), "id": str(doc_id)}).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
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


def _parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        return datetime.fromisoformat(since)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": f"since 형식 오류 (ISO8601 필요): {e}",
            },
        ) from e


async def _resolve_topic_type(
    db: AsyncSession, topic_id: UUID, user_id: UUID
) -> str:
    """cso vs leaf 판정. enumeration 차단을 위해 부재·타인 leaf 모두 404."""
    cso_exists = (
        await db.execute(
            select(CSOTopic.cso_topic_id).where(CSOTopic.cso_topic_id == topic_id)
        )
    ).scalar_one_or_none()
    if cso_exists is not None:
        return "cso"
    leaf_owner = (
        await db.execute(
            select(DynamicLeafTopic.user_id).where(
                DynamicLeafTopic.leaf_topic_id == topic_id
            )
        )
    ).scalar_one_or_none()
    if leaf_owner is not None and leaf_owner == user_id:
        return "leaf"
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": ErrorCode.TOPIC_NOT_FOUND.value,
            "message": "토픽을 찾을 수 없습니다.",
        },
    )


async def list_topic_documents(
    db: AsyncSession,
    user_id: UUID,
    topic_id: UUID,
    since: str | None,
    cursor: str | None,
    limit: int,
) -> TopicDocumentsResponse:
    """topic 화면 문서 list (cso 또는 leaf)."""
    topic_type = await _resolve_topic_type(db, topic_id, user_id)
    since_dt = _parse_since(since)

    filter_column = (
        DocumentTopic.cso_topic_id
        if topic_type == "cso"
        else DocumentTopic.leaf_topic_id
    )
    # (S-04) ORDER BY / cursor / since 모두 coalesce(published_at, created_at) 정합.
    # PostgreSQL 룰: SELECT DISTINCT 의 ORDER BY 표현은 select list 에 있어야 함 →
    # sort_ts 를 SELECT 에 label 로 노출 (round 3 후속 fix).
    sort_ts = func.coalesce(Document.published_at, Document.created_at).label("sort_ts")
    stmt = (
        select(Document, Source.name, Source.source_type, sort_ts)
        .join(DocumentTopic, DocumentTopic.document_id == Document.document_id)
        .join(Source, Source.source_id == Document.source_id)
        .where(filter_column == topic_id)
        .distinct()
        .order_by(
            desc(sort_ts),
            desc(Document.document_id),
        )
    )
    if since_dt is not None:
        stmt = stmt.where(sort_ts >= since_dt)
    if cursor:
        ts, did = _decode_cursor(cursor)
        stmt = stmt.where(
            (sort_ts < ts)
            | ((sort_ts == ts) & (Document.document_id < did))
        )

    # A8 filter PLACEHOLDER:
    #   - NotInterestedTopic (user_id, cso_topic_id) → 제외
    #   - HiddenDocument (user_id, document_id) → 제외
    #   - ClickbaitResult.decision='clickbait' AND settings.CLICKBAIT_ENABLED → 제외
    # 본 PR 은 schema 만 정의 — 실제 filter 는 A8 PR 에서 추가.

    stmt = stmt.limit(limit + 1)
    rows = list((await db.execute(stmt)).all())
    has_more = len(rows) > limit
    page = rows[:limit]

    # CSO topic 매핑 일괄 조회 (related_topics)
    document_ids = [row[0].document_id for row in page]
    related_map: dict[UUID, list[CSOTopicSummary]] = {did: [] for did in document_ids}
    if document_ids:
        related_stmt = (
            select(
                DocumentTopic.document_id,
                CSOTopic.cso_topic_id,
                CSOTopic.label,
            )
            .join(CSOTopic, CSOTopic.cso_topic_id == DocumentTopic.cso_topic_id)
            .where(DocumentTopic.document_id.in_(document_ids))
        )
        for r in await db.execute(related_stmt):
            related_map[r.document_id].append(
                CSOTopicSummary(cso_topic_id=r.cso_topic_id, label=r.label)
            )

    items: list[DocumentSummary] = []
    # row tuple = (Document, source_name, source_type_raw, sort_ts) — sort_ts 는 ORDER BY
    # 용도로 SELECT 에 노출돼 있으므로 응답 구성 시 unpacking 에서 무시.
    for doc, source_name, source_type_raw, _sort_ts in page:
        # published_at NULL 인 경우 created_at 으로 fallback (contracts.DocumentSummary 가
        # nullable 미허용 — Q1=A 결정으로 contracts 변경 X, 응답 시점 fallback 채택).
        ts_for_response = doc.published_at if doc.published_at else doc.created_at
        items.append(
            DocumentSummary(
                document_id=doc.document_id,
                title=doc.title,
                source_name=source_name,
                source_type=SourceType(source_type_raw),
                published_at=ts_for_response,
                url=doc.url,
                related_topics=related_map.get(doc.document_id, []),
            )
        )
    next_cursor: str | None = None
    if has_more and page:
        last_doc = page[-1][0]
        cursor_ts = last_doc.published_at or last_doc.created_at
        next_cursor = _encode_cursor(cursor_ts, last_doc.document_id)

    return TopicDocumentsResponse(
        topic_type="cso" if topic_type == "cso" else "leaf",
        topic_id=topic_id,
        items=items,
        meta=PageMeta(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(items),
        ),
    )


__all__ = ["list_topic_documents"]
