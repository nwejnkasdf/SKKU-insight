"""topic router — Phase 0a stub.

docs: api/topics.md, algorithms/cso-mapping.md, algorithms/cso-topic-traversal.md.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.contracts import LeafTopicStatus, PagedResponse, TraversalStatus

from .schemas import (
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
async def get_cso_clusters() -> ClustersResponse:
    """24 시간 캐시. locale 헤더로 한·영 분기."""
    raise NotImplementedError("Phase 0b A3에서 구현")


@router.get(
    "/cso/{cso_topic_id}",
    response_model=CSOTopicDetail,
    summary="CSO 토픽 상세",
)
async def get_cso_topic_detail(cso_topic_id: UUID) -> CSOTopicDetail:
    raise NotImplementedError("Phase 0b A3에서 구현")


@router.get(
    "/cso/{cso_topic_id}/adjacent",
    response_model=AdjacentResponse,
    summary="인접 CSO 토픽",
)
async def get_cso_adjacent(
    cso_topic_id: UUID,
    hops: int = Query(default=1, ge=1, le=3),
) -> AdjacentResponse:
    raise NotImplementedError("Phase 0b A3에서 구현")


@router.get(
    "/cso/{cso_topic_id}/descendants",
    response_model=DescendantsResponse,
    summary="후손 CSO 토픽",
)
async def get_cso_descendants(cso_topic_id: UUID) -> DescendantsResponse:
    raise NotImplementedError("Phase 0b A3에서 구현")


@router.get(
    "/leaves",
    response_model=PagedResponse[DynamicLeafTopic],
    summary="자기 동적 리프 토픽 목록",
)
async def list_leaves(
    status: LeafTopicStatus | None = Query(default=LeafTopicStatus.ACTIVE),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[DynamicLeafTopic]:
    raise NotImplementedError("Phase 0b A7에서 구현")


@router.get(
    "/leaves/{leaf_topic_id}",
    response_model=DynamicLeafTopic,
    summary="동적 리프 상세",
)
async def get_leaf_detail(leaf_topic_id: UUID) -> DynamicLeafTopic:
    raise NotImplementedError("Phase 0b A7에서 구현")


@router.get(
    "/{topic_id}/documents",
    response_model=TopicDocumentsResponse,
    summary="토픽 상세 화면 문서 (UI-03)",
)
async def get_topic_documents(
    topic_id: UUID,
    since: str | None = Query(default=None, description="ISO8601 timestamp"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> TopicDocumentsResponse:
    """NotInterestedTopic 제외 + HiddenDocument 제외 + clickbait 제외 (FR-31)."""
    raise NotImplementedError("Phase 0b A4·A8에서 구현")


@router.get(
    "/traces",
    response_model=PagedResponse[TraversalTraceSummary],
    summary="자기 traversal trace 목록",
)
async def list_traces(
    status: TraversalStatus | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[TraversalTraceSummary]:
    """active+stale 만 default. ?status=archived 명시 시 archived 포함."""
    raise NotImplementedError("Phase 0b A7에서 구현")


@router.get(
    "/traces/{trace_id}",
    response_model=TraversalTraceDetail,
    summary="trace 상세 (path + 산하 leaf)",
)
async def get_trace_detail(trace_id: UUID) -> TraversalTraceDetail:
    """score_tail 은 일반 사용자 응답에서 마스킹 (NFR-04). 관리자 endpoint 별도."""
    raise NotImplementedError("Phase 0b A7에서 구현")
