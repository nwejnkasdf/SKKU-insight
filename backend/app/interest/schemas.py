"""interest Pydantic schemas — docs/api/interest.md.

본 파일은 /interest, /events, /feedback 세 영역의 schema 를 모두 담당.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.contracts import EventType, InterestBucket

# ============================================================
# Event (행동 로그)
# ============================================================


class EventRequest(BaseModel):
    """행동 로그 1건. dwell_tick 은 30초 단위 tick."""

    event_type: EventType
    document_id: UUID | None = None
    cso_topic_id: UUID | None = None
    leaf_topic_id: UUID | None = None
    dwell_ms: int | None = None
    occurred_at: datetime
    client_request_id: str  # idempotency key


class EventBatchRequest(BaseModel):
    """최대 50 건 batch. dwell_tick 폭증 완화 (concurrency.md §6)."""

    events: list[EventRequest] = Field(min_length=1, max_length=50)


class EventResponse(BaseModel):
    event_id: UUID
    accepted: bool
    server_received_at: datetime


# ============================================================
# Interest state
# ============================================================


class InterestTopicView(BaseModel):
    """관심 토픽 노출. NFR-04: 점수 자체 노출 X — bucket 만."""

    cso_topic_id: UUID | None = None
    leaf_topic_id: UUID | None = None
    label: str
    bucket: InterestBucket


class InterestStateResponse(BaseModel):
    user_id: UUID
    topics: list[InterestTopicView]
    updated_at: datetime


# ============================================================
# Feedback (명시 액션)
# ============================================================


class SaveFeedbackRequest(BaseModel):
    document_id: UUID


class HideFeedbackRequest(BaseModel):
    document_id: UUID


class NotInterestedRequest(BaseModel):
    """토픽 또는 문서 단위 명시 거부.

    cso_topic_id / leaf_topic_id / document_id 중 1개 이상 필수
    (Phase 0b 가 model_validator 추가).
    """

    cso_topic_id: UUID | None = None
    leaf_topic_id: UUID | None = None
    document_id: UUID | None = None
