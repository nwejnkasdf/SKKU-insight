"""topic router — A3 7 endpoint 본문 + documents NotImplementedError 유지.

docs/api/topics.md 비즈니스 룰:
- consent_gate 자동 적용 (`/topics(/.*)?` PROTECTED_PATTERNS).
- 사용자별 격리 (leaves/traces): JWT user_id 기반 WHERE.
- score_tail NFR-04 마스킹 (trace_service 에서 항상 None — 결정 7).
- /topics/{topic_id}/documents: NotImplementedError 유지 (결정 3, A4·A5·A8 의존).
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import LeafTopicStatus, PagedResponse, TraversalStatus
from app.db.models import User
from app.db.session import get_session
from app.redis import get_redis
from app.security.deps import get_current_user
from app.topic import cso_service, leaf_service, trace_service
from app.topic.schemas import (
    AdjacentResponse,
    ClustersResponse,
    CSOTopicDetail,
    DescendantsResponse,
    DynamicLeafTopic,
    TopicDocumentsResponse,
    TraversalTraceDetail,
    TraversalTraceSummary,
)

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get(
    "/cso/clusters",
    response_model=ClustersResponse,
    summary="12 CSO 클러스터 (온보딩·설정 공통, FR-08·13)",
)
async def get_cso_clusters(
    db: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ClustersResponse:
    """24h Redis 캐시. 12 entry 정확. locale 헤더 없이 한국어 description."""
    redis = get_redis("cache")
    return await cso_service.get_clusters(db, redis)


@router.get(
    "/cso/{cso_topic_id}",
    response_model=CSOTopicDetail,
    summary="CSO 토픽 상세",
)
async def get_cso_topic_detail(
    cso_topic_id: UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(get_current_user)],
) -> CSOTopicDetail:
    """parents 는 cso_topic_parent SOR (다중 부모 보존)."""
    g = request.app.state.cso_graph
    return await cso_service.get_topic_detail(db, g, cso_topic_id)


@router.get(
    "/cso/{cso_topic_id}/adjacent",
    response_model=AdjacentResponse,
    summary="인접 CSO 토픽",
)
async def get_cso_adjacent(
    cso_topic_id: UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(get_current_user)],
    hops: int = Query(default=1, ge=1, le=3),
) -> AdjacentResponse:
    g = request.app.state.cso_graph
    return await cso_service.get_adjacent(db, g, cso_topic_id, hops)


@router.get(
    "/cso/{cso_topic_id}/descendants",
    response_model=DescendantsResponse,
    summary="후손 CSO 토픽",
)
async def get_cso_descendants(
    cso_topic_id: UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(get_current_user)],
) -> DescendantsResponse:
    g = request.app.state.cso_graph
    return await cso_service.get_descendants(db, g, cso_topic_id)


@router.get(
    "/leaves",
    response_model=PagedResponse[DynamicLeafTopic],
    summary="자기 동적 리프 토픽 목록",
)
async def list_leaves(
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    status: LeafTopicStatus | None = Query(default=LeafTopicStatus.ACTIVE),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[DynamicLeafTopic]:
    """사용자별 격리. default status=ACTIVE (결정 8)."""
    return await leaf_service.list_leaves(
        db, user.user_id, status, cursor, limit
    )


@router.get(
    "/leaves/{leaf_topic_id}",
    response_model=DynamicLeafTopic,
    summary="동적 리프 상세",
)
async def get_leaf_detail(
    leaf_topic_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> DynamicLeafTopic:
    """부재·타인 row 모두 404 topic.not_found (결정 11, enumeration 차단)."""
    return await leaf_service.get_leaf_detail(db, user.user_id, leaf_topic_id)


@router.get(
    "/{topic_id}/documents",
    response_model=TopicDocumentsResponse,
    summary="토픽 상세 화면 문서 (UI-03)",
)
async def get_topic_documents(
    topic_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    since: str | None = Query(default=None, description="ISO8601 timestamp"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> TopicDocumentsResponse:
    """NotInterestedTopic 제외 + HiddenDocument 제외 + clickbait 제외 (FR-31).

    A3 결정 3: NotImplementedError 유지. A4 (Document) · A5 (clickbait) · A8 (filter)
    완료 후 본 endpoint 본문 채움.
    """
    raise NotImplementedError("A4·A8 에서 Document/Filter 본문 구현")


@router.get(
    "/traces",
    response_model=PagedResponse[TraversalTraceSummary],
    summary="자기 traversal trace 목록",
)
async def list_traces(
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    status: TraversalStatus | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[TraversalTraceSummary]:
    """?status 미제공 → active + stale (archived 제외). ?status=archived 명시 시 archived 만."""
    return await trace_service.list_traces(
        db, user.user_id, status, cursor, limit
    )


@router.get(
    "/traces/{trace_id}",
    response_model=TraversalTraceDetail,
    summary="trace 상세 (path + 산하 leaf)",
)
async def get_trace_detail(
    trace_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> TraversalTraceDetail:
    """score_tail 은 일반 사용자 응답에서 마스킹 None (NFR-04, 결정 7).

    부재·타인 row 모두 404 topic.not_found (결정 11).
    """
    return await trace_service.get_trace_detail(db, user.user_id, trace_id)


__all__ = ["router"]
