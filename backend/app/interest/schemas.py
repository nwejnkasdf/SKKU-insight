"""interest Pydantic schemas — docs/api/interest.md.

본 파일은 /interest, /events, /feedback 세 영역의 schema 를 모두 담당.

A6 (2026-05-17) 추가:
- BatchResponse: /events/batch 207 Multi-Status 응답 envelope.
- NotInterestedRequest.model_validator: cso/leaf/document_id 중 1+ 필수.
"""
from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

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
    # 207 Multi-Status batch 응답에서 실패 entry 의 사유.
    error_code: str | None = None


class BatchResponse(BaseModel):
    """POST /events/batch 207 Multi-Status 응답.

    items: list[EventResponse] — 성공/실패 entry 모두 포함, accepted 필드로 구분.
    total_accepted: int — 성공한 entry 수.
    """

    items: list[EventResponse]
    total_accepted: int


# ============================================================
# Interest state
# ============================================================


class InterestTopicView(BaseModel):
    """관심 토픽 노출. NFR-04: 점수 자체 노출 X — bucket 만.

    (C-60, 2026-05-25) `is_onboarding_selected` 신규 — 사용자가 onboarding 시 직접
    선택한 cluster (또는 1-hop boost 자식) 표시. UI 의 "초기 seed" view 가 onboarding
    선택 정합. backend SQL = `boost_applied_at_active_day IS NOT NULL`.
    """

    cso_topic_id: UUID | None = None
    leaf_topic_id: UUID | None = None
    label: str
    bucket: InterestBucket
    is_onboarding_selected: bool = False


class InterestStateResponse(BaseModel):
    user_id: UUID
    topics: list[InterestTopicView]
    updated_at: datetime | None = None


# ============================================================
# Feedback (명시 액션)
# ============================================================


class SaveFeedbackRequest(BaseModel):
    document_id: UUID
    client_request_id: str


class HideFeedbackRequest(BaseModel):
    document_id: UUID
    client_request_id: str


class NotInterestedRequest(BaseModel):
    """문서 단위 관심 없음 또는 토픽 단위 분야 줄이기.

    cso_topic_id / leaf_topic_id / document_id 중 1개 이상 필수. 셋 다 None 이면 422.
    document_id 단독 시 해당 문서만 추천 큐에서 제외하고 토픽 posterior 는 변경하지 않는다.
    cso_topic_id / leaf_topic_id 직접 지정 시에만 토픽 선호도 감소로 처리한다.
    """

    cso_topic_id: UUID | None = None
    leaf_topic_id: UUID | None = None
    document_id: UUID | None = None
    client_request_id: str

    @model_validator(mode="after")
    def validate_at_least_one(self) -> Self:
        if (
            self.cso_topic_id is None
            and self.leaf_topic_id is None
            and self.document_id is None
        ):
            raise ValueError(
                "cso_topic_id / leaf_topic_id / document_id 중 최소 1개는 필수."
            )
        return self


__all__ = [
    "BatchResponse",
    "EventBatchRequest",
    "EventRequest",
    "EventResponse",
    "HideFeedbackRequest",
    "InterestStateResponse",
    "InterestTopicView",
    "NotInterestedRequest",
    "SaveFeedbackRequest",
]
