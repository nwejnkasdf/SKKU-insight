"""topic Pydantic schemas — docs/api/topics.md."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.contracts import (
    CSOTopicSummary,
    DocumentSummary,
    LeafTopicStatus,
    PageMeta,
    TraversalStatus,
)


class CSOCluster(BaseModel):
    """12 CSO 클러스터 (온보딩·설정 공통). description_ko 한국어 설명."""

    cso_topic_id: UUID
    label: str
    description_ko: str
    document_count: int


class ClustersResponse(BaseModel):
    """`GET /topics/cso/clusters` — 정확히 12 개."""

    clusters: list[CSOCluster]


class CSOTopicDetail(BaseModel):
    """CSO 토픽 상세 + 부모 정보."""

    cso_topic_id: UUID
    label: str
    uri: str
    parent_topic_id: UUID | None = None
    parents: list[CSOTopicSummary]
    children_count: int


class AdjacentResponse(BaseModel):
    """1-hop / N-hop 인접 토픽."""

    seed_id: UUID
    hops: int
    topics: list[CSOTopicSummary]


class DescendantsResponse(BaseModel):
    """후손 CSO 토픽 (depth-first or BFS, 구현 시 결정)."""

    seed_id: UUID
    topics: list[CSOTopicSummary]


class DynamicLeafTopic(BaseModel):
    """사용자별 동적 리프 토픽."""

    leaf_topic_id: UUID
    label: str
    confidence: float
    status: LeafTopicStatus
    created_at: datetime
    cso_topic_ids: list[UUID]
    merged_into_leaf_topic_id: UUID | None = None


class TopicDocumentsResponse(BaseModel):
    """`GET /topics/{topic_id}/documents` — 토픽 상세 화면(UI-03) 문서 목록."""

    topic_type: Literal["cso", "leaf"]
    topic_id: UUID
    items: list[DocumentSummary]
    meta: PageMeta


class TraversalTraceSummary(BaseModel):
    """trace 목록 view (디버그·설정용)."""

    trace_id: UUID
    path_labels: list[str]
    status: TraversalStatus
    started_active_day: int
    last_activity_active_day: int
    leaf_count: int


class TraversalTraceDetail(BaseModel):
    """trace 상세 — path 노드 + 산하 leaf. score_tail 은 NFR-04 마스킹."""

    trace_id: UUID
    path: list[CSOTopicSummary]
    status: TraversalStatus
    leaves: list[DynamicLeafTopic]
    started_active_day: int
    last_activity_active_day: int
    score_tail: float | None = None  # 일반 사용자 응답에서는 None (마스킹)
