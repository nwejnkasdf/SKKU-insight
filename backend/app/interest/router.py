"""interest router — /interest, /events, /feedback 영역 포함. Phase 0a stub.

docs: api/interest.md, algorithms/interest-bayesian.md, sdd/concurrency.md §3·4·6.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.contracts import DocumentSummary, PagedResponse

from .schemas import (
    EventBatchRequest,
    EventRequest,
    EventResponse,
    HideFeedbackRequest,
    InterestStateResponse,
    NotInterestedRequest,
    SaveFeedbackRequest,
)

router = APIRouter()


# ============================================================
# /interest
# ============================================================


@router.get(
    "/interest/state",
    response_model=InterestStateResponse,
    tags=["interest"],
    summary="자기 관심 상태 조회 (NFR-04 마스킹)",
)
async def get_interest_state() -> InterestStateResponse:
    """점수 자체 노출 X — bucket 만. 관리자 응답은 admin 모듈."""
    raise NotImplementedError("Phase 0b A6에서 구현")


# ============================================================
# /events
# ============================================================


@router.post(
    "/events",
    response_model=EventResponse,
    tags=["events"],
    summary="행동 로그 1 건 (FR-17·18)",
)
async def post_event(req: EventRequest) -> EventResponse:
    """user-level Redis lock + atomic SQL UPSERT (concurrency.md §3·§4)."""
    raise NotImplementedError("Phase 0b A6에서 구현")


@router.post(
    "/events/batch",
    response_model=list[EventResponse],
    tags=["events"],
    summary="행동 로그 batch (최대 50)",
)
async def post_events_batch(req: EventBatchRequest) -> list[EventResponse]:
    """5초 batch flush 윈도우와 결합 (concurrency.md §6)."""
    raise NotImplementedError("Phase 0b A6에서 구현")


# ============================================================
# /feedback
# ============================================================


@router.post(
    "/feedback/save",
    response_model=EventResponse,
    tags=["feedback"],
    summary="저장 (FR-19)",
)
async def post_feedback_save(req: SaveFeedbackRequest) -> EventResponse:
    """SavedDocument INSERT + UserEvent + 베이지안 atomic update + 추천 캐시 invalidate."""
    raise NotImplementedError("Phase 0b A6에서 구현")


@router.post(
    "/feedback/hide",
    response_model=EventResponse,
    tags=["feedback"],
    summary="숨김",
)
async def post_feedback_hide(req: HideFeedbackRequest) -> EventResponse:
    raise NotImplementedError("Phase 0b A6에서 구현")


@router.post(
    "/feedback/not-interested",
    response_model=EventResponse,
    tags=["feedback"],
    summary="관심 없음 (토픽 또는 문서)",
)
async def post_feedback_not_interested(req: NotInterestedRequest) -> EventResponse:
    raise NotImplementedError("Phase 0b A6에서 구현")


@router.get(
    "/feedback/saved",
    response_model=PagedResponse[DocumentSummary],
    tags=["feedback"],
    summary="저장 목록 (UI-05)",
)
async def list_saved(
    cursor: str | None = None,
    limit: int = 20,
) -> PagedResponse[DocumentSummary]:
    raise NotImplementedError("Phase 0b A6에서 구현")


@router.get(
    "/feedback/hidden",
    response_model=PagedResponse[DocumentSummary],
    tags=["feedback"],
    summary="숨김 목록 (UI-05)",
)
async def list_hidden(
    cursor: str | None = None,
    limit: int = 20,
) -> PagedResponse[DocumentSummary]:
    raise NotImplementedError("Phase 0b A6에서 구현")


@router.delete(
    "/feedback/saved/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["feedback"],
    summary="저장 해제",
)
async def delete_saved(document_id: UUID) -> Response:
    """동의 비활성이어도 허용 (사용자가 본인 데이터 정리 가능)."""
    raise NotImplementedError("Phase 0b A6에서 구현")
