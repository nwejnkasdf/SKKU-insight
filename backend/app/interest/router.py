"""interest router — /interest, /events, /feedback 영역 (9 endpoint).

docs: api/interest.md, algorithms/interest-bayesian.md, sdd/concurrency.md §3·4·6.

본 구현은 1차 시연 단순화로 /events·/feedback 모두 즉시 ingest (EventBuffer 는
lifespan background task 로 등록만 — 추후 폭증 대비 활성 가능). dwell_tick 폭증은
Redis cap 으로 1차 완화.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.contracts import (
    DocumentSummary,
    ErrorCode,
    ErrorResponse,
    EventType,
    PagedResponse,
    PageMeta,
    RedisKey,
    SourceType,
)
from app.db.models import (
    CSOTopic,
    Document,
    DynamicLeafTopic,
    HiddenDocument,
    Source,
    User,
)
from app.db.session import get_session
from app.events.active_day import maybe_increment_active_day
from app.interest import service as interest_service
from app.interest.bucket import bucket_for, bucket_sort_key
from app.interest.config_loader import (
    EventWeights,
    InterestParams,
    get_event_weights,
    get_interest_params,
)
from app.redis import get_redis
from app.security.deps import get_current_user, require_consent_active

from .schemas import (
    BatchResponse,
    EventBatchRequest,
    EventRequest,
    EventResponse,
    HideFeedbackRequest,
    InterestStateResponse,
    InterestTopicView,
    NotInterestedRequest,
    SaveFeedbackRequest,
)

router = APIRouter()

# 캐시 lookup 용 redis client — request 마다 동일.
def _redis() -> aioredis.Redis:
    return get_redis("default")


def _http_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    *,
    request: Request,
    details: dict[str, Any] | None = None,
) -> HTTPException:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        code=code, message=message, details=details, request_id=request_id
    ).model_dump(mode="json")
    return HTTPException(status_code=status_code, detail=body)


async def _load_params_and_weights(
    redis: aioredis.Redis, db: AsyncSession
) -> tuple[InterestParams, EventWeights]:
    params = await get_interest_params(redis, db)
    weights = await get_event_weights(redis, db)
    return params, weights


async def _ensure_active_day(
    db: AsyncSession, user: User, now: datetime
) -> tuple[int, User]:
    """active_day_counter 갱신 + 갱신된 값 반환. user 객체에도 반영."""
    current = await maybe_increment_active_day(db, user.user_id, now.date())
    user.active_day_counter = current
    return current, user


# ============================================================
# GET /interest/state
# ============================================================


@router.get(
    "/interest/state",
    response_model=InterestStateResponse,
    tags=["interest"],
    summary="자기 관심 상태 조회 (NFR-04 마스킹)",
)
async def get_interest_state(
    request: Request,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> InterestStateResponse:
    """점수 자체 노출 X — bucket 만. HIGH→MEDIUM→LOW→NEUTRAL, 내부 long_score DESC, max 50."""
    redis = _redis()
    params = await get_interest_params(redis, db)
    rows = await interest_service.fetch_user_state(db, user.user_id, limit=50)

    # cso_topic_id / leaf_topic_id 의 label lookup
    cso_ids = [row.cso_topic_id for row in rows if row.cso_topic_id is not None]
    leaf_ids = [row.leaf_topic_id for row in rows if row.leaf_topic_id is not None]
    cso_labels: dict[UUID, str] = {}
    leaf_labels: dict[UUID, str] = {}
    if cso_ids:
        result = await db.execute(
            select(CSOTopic.cso_topic_id, CSOTopic.label).where(
                CSOTopic.cso_topic_id.in_(cso_ids)
            )
        )
        for r in result:
            cso_labels[r.cso_topic_id] = r.label
    if leaf_ids:
        result = await db.execute(
            select(
                DynamicLeafTopic.leaf_topic_id, DynamicLeafTopic.label
            ).where(DynamicLeafTopic.leaf_topic_id.in_(leaf_ids))
        )
        for r in result:
            leaf_labels[r.leaf_topic_id] = r.label

    topics: list[InterestTopicView] = []
    for row in rows:
        bucket = bucket_for(row.long_score, row.short_score, params)
        label = ""
        if row.leaf_topic_id is not None:
            label = leaf_labels.get(row.leaf_topic_id, "")
        elif row.cso_topic_id is not None:
            label = cso_labels.get(row.cso_topic_id, "")
        topics.append(
            InterestTopicView(
                cso_topic_id=row.cso_topic_id,
                leaf_topic_id=row.leaf_topic_id,
                label=label,
                bucket=bucket,
            )
        )
    # bucket HIGH→MEDIUM→LOW→NEUTRAL 정렬 (안정 정렬로 내부 long_score DESC 유지)
    topics.sort(key=lambda t: bucket_sort_key(t.bucket))
    updated_at = await interest_service.fetch_max_updated_at(db, user.user_id)
    return InterestStateResponse(
        user_id=user.user_id, topics=topics, updated_at=updated_at
    )


# ============================================================
# POST /events
# ============================================================


async def _ingest_one_event(
    db: AsyncSession,
    redis: aioredis.Redis,
    settings: Settings,
    params: InterestParams,
    weights: EventWeights,
    *,
    request_state: Request,
    user: User,
    req: EventRequest,
    cache_invalidate: bool,
) -> interest_service.IngestResult:
    """단일 event ingest. EventDuplicateError → 409. InvalidEventTargetError → 422.

    Codex C-02 fix: store_idempotent (Redis SETEX) 는 caller (post_event / batch) 가
    db.commit() 성공 후 호출. 본 함수는 service.IngestResult 만 반환.
    """
    cso_graph = getattr(request_state.app.state, "cso_graph", None)
    try:
        return await interest_service.ingest_event_atomic(
            db,
            redis,
            cso_graph,
            settings,
            params,
            weights,
            user=user,
            event_type=req.event_type,
            document_id=req.document_id,
            cso_topic_id=req.cso_topic_id,
            leaf_topic_id=req.leaf_topic_id,
            dwell_ms=req.dwell_ms,
            client_request_id=req.client_request_id,
            occurred_at=req.occurred_at,
            active_day=user.active_day_counter,
            cache_invalidate=cache_invalidate,
        )
    except interest_service.EventDuplicateError as exc:
        raise _http_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.EVENT_DUPLICATE,
            "동일 client_request_id 가 다른 payload 로 재호출됨.",
            request=request_state,
            details={
                "existing_event_id": (
                    str(exc.existing_event_id) if exc.existing_event_id else None
                )
            },
        ) from exc
    except interest_service.InvalidEventTargetError as exc:
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCode.EVENT_INVALID_TARGET,
            str(exc),
            request=request_state,
        ) from exc


async def _store_idempotent_after_commit(
    redis: aioredis.Redis,
    result: interest_service.IngestResult,
    *,
    user_id: UUID,
    settings: Settings,
) -> None:
    """Codex C-02: db.commit() 성공 후 Redis SETEX. duplicate(이미 caching 됨) 시 skip."""
    if result.duplicate or result.payload_hash is None or result.client_request_id is None:
        return
    from app.interest.idempotency import store_idempotent

    await store_idempotent(
        redis,
        user_id=user_id,
        client_request_id=result.client_request_id,
        payload_hash=result.payload_hash,
        event_id=result.event_id,
        ttl_seconds=settings.EVENT_DUPLICATE_CACHE_TTL_SECONDS,
    )


def _is_cache_invalidating(event_type: EventType) -> bool:
    return event_type in {EventType.SAVE, EventType.HIDE, EventType.NOT_INTERESTED}


@router.post(
    "/events",
    response_model=EventResponse,
    tags=["events"],
    summary="행동 로그 1 건 (FR-17·18)",
)
async def post_event(
    request: Request,
    req: EventRequest,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EventResponse:
    """user-level Redis lock + atomic SQL UPSERT (concurrency.md §3·§4).

    1차 시연: EventBuffer 미사용, 즉시 ingest. dwell_tick 폭증은 Redis cap 으로 완화.
    """
    now = datetime.now(UTC)
    await _ensure_active_day(db, user, now)
    redis = _redis()
    params, weights = await _load_params_and_weights(redis, db)
    result = await _ingest_one_event(
        db,
        redis,
        settings,
        params,
        weights,
        request_state=request,
        user=user,
        req=req,
        cache_invalidate=_is_cache_invalidating(req.event_type),
    )
    await db.commit()
    await _store_idempotent_after_commit(
        redis, result, user_id=user.user_id, settings=settings
    )
    return EventResponse(
        event_id=result.event_id,
        accepted=result.accepted,
        server_received_at=result.server_received_at,
    )


@router.post(
    "/events/batch",
    response_model=BatchResponse,
    status_code=status.HTTP_207_MULTI_STATUS,
    tags=["events"],
    summary="행동 로그 batch (최대 50)",
)
async def post_events_batch(
    request: Request,
    req: EventBatchRequest,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> BatchResponse:
    """207 Multi-Status. 성공한 entry 만 accepted=True, 실패는 accepted=False + error_code."""
    now = datetime.now(UTC)
    await _ensure_active_day(db, user, now)
    redis = _redis()
    params, weights = await _load_params_and_weights(redis, db)
    cso_graph = getattr(request.app.state, "cso_graph", None)
    items: list[EventResponse] = []
    accepted_count = 0
    successful_results: list[interest_service.IngestResult] = []
    for entry in req.events:
        try:
            result = await interest_service.ingest_event_atomic(
                db,
                redis,
                cso_graph,
                settings,
                params,
                weights,
                user=user,
                event_type=entry.event_type,
                document_id=entry.document_id,
                cso_topic_id=entry.cso_topic_id,
                leaf_topic_id=entry.leaf_topic_id,
                dwell_ms=entry.dwell_ms,
                client_request_id=entry.client_request_id,
                occurred_at=entry.occurred_at,
                active_day=user.active_day_counter,
                cache_invalidate=_is_cache_invalidating(entry.event_type),
            )
        except interest_service.EventDuplicateError as exc:
            items.append(
                EventResponse(
                    event_id=exc.existing_event_id or UUID(int=0),
                    accepted=False,
                    server_received_at=now,
                    error_code=ErrorCode.EVENT_DUPLICATE.value,
                )
            )
            continue
        except interest_service.InvalidEventTargetError:
            items.append(
                EventResponse(
                    event_id=UUID(int=0),
                    accepted=False,
                    server_received_at=now,
                    error_code=ErrorCode.EVENT_INVALID_TARGET.value,
                )
            )
            continue
        items.append(
            EventResponse(
                event_id=result.event_id,
                accepted=True,
                server_received_at=result.server_received_at,
            )
        )
        successful_results.append(result)
        accepted_count += 1
    await db.commit()
    # Codex C-02: 모든 commit 성공 후 Redis SETEX (per entry).
    for r in successful_results:
        await _store_idempotent_after_commit(
            redis, r, user_id=user.user_id, settings=settings
        )
    return BatchResponse(items=items, total_accepted=accepted_count)


# ============================================================
# /feedback/save
# ============================================================


@router.post(
    "/feedback/save",
    response_model=EventResponse,
    tags=["feedback"],
    summary="저장 (FR-19)",
)
async def post_feedback_save(
    request: Request,
    req: SaveFeedbackRequest,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EventResponse:
    """SavedDocument INSERT + UserEvent + 베이지안 + recommendation cache invalidate."""
    now = datetime.now(UTC)
    await _ensure_active_day(db, user, now)
    redis = _redis()
    params, weights = await _load_params_and_weights(redis, db)
    cso_graph = getattr(request.app.state, "cso_graph", None)
    try:
        result, already_saved = await interest_service.save_feedback(
            db,
            redis,
            cso_graph,
            settings,
            params,
            weights,
            user=user,
            document_id=req.document_id,
            client_request_id=req.client_request_id,
            occurred_at=now,
            active_day=user.active_day_counter,
        )
    except interest_service.EventDuplicateError as exc:
        raise _http_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.EVENT_DUPLICATE,
            "동일 client_request_id 가 다른 payload 로 재호출됨.",
            request=request,
            details={
                "existing_event_id": (
                    str(exc.existing_event_id) if exc.existing_event_id else None
                )
            },
        ) from exc
    if already_saved and not result.duplicate:
        # SavedDocument 가 이미 있었으나 이벤트는 새로 기록. 명시 의도 보존.
        # api/interest.md feedback.already_saved 는 사용자가 동일 카드 재저장 시도 케이스
        # — 본 구현은 INSERT ON CONFLICT DO NOTHING + 200 응답 (Bayesian 누적). 409 는
        # 미사용 (idempotent 의도).
        pass
    await db.commit()
    await _store_idempotent_after_commit(
        redis, result, user_id=user.user_id, settings=settings
    )
    return EventResponse(
        event_id=result.event_id,
        accepted=result.accepted,
        server_received_at=result.server_received_at,
    )


# ============================================================
# /feedback/hide
# ============================================================


@router.post(
    "/feedback/hide",
    response_model=EventResponse,
    tags=["feedback"],
    summary="숨김",
)
async def post_feedback_hide(
    request: Request,
    req: HideFeedbackRequest,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EventResponse:
    now = datetime.now(UTC)
    await _ensure_active_day(db, user, now)
    redis = _redis()
    params, weights = await _load_params_and_weights(redis, db)
    cso_graph = getattr(request.app.state, "cso_graph", None)
    try:
        result = await interest_service.hide_feedback(
            db,
            redis,
            cso_graph,
            settings,
            params,
            weights,
            user=user,
            document_id=req.document_id,
            client_request_id=req.client_request_id,
            occurred_at=now,
            active_day=user.active_day_counter,
        )
    except interest_service.EventDuplicateError as exc:
        raise _http_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.EVENT_DUPLICATE,
            "동일 client_request_id 가 다른 payload 로 재호출됨.",
            request=request,
        ) from exc
    await db.commit()
    await _store_idempotent_after_commit(
        redis, result, user_id=user.user_id, settings=settings
    )
    return EventResponse(
        event_id=result.event_id,
        accepted=result.accepted,
        server_received_at=result.server_received_at,
    )


# ============================================================
# /feedback/not-interested
# ============================================================


@router.post(
    "/feedback/not-interested",
    response_model=EventResponse,
    tags=["feedback"],
    summary="관심 없음 (토픽 또는 문서)",
)
async def post_feedback_not_interested(
    request: Request,
    req: NotInterestedRequest,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EventResponse:
    """하이브리드 (정렬 2): Bayesian P1-4 분배 + NotInterestedTopic 최고 confidence 1건."""
    now = datetime.now(UTC)
    await _ensure_active_day(db, user, now)
    redis = _redis()
    params, weights = await _load_params_and_weights(redis, db)
    cso_graph = getattr(request.app.state, "cso_graph", None)
    try:
        result = await interest_service.not_interested_feedback(
            db,
            redis,
            cso_graph,
            settings,
            params,
            weights,
            user=user,
            document_id=req.document_id,
            cso_topic_id=req.cso_topic_id,
            leaf_topic_id=req.leaf_topic_id,
            client_request_id=req.client_request_id,
            occurred_at=now,
            active_day=user.active_day_counter,
        )
    except interest_service.EventDuplicateError as exc:
        raise _http_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.EVENT_DUPLICATE,
            "동일 client_request_id 가 다른 payload 로 재호출됨.",
            request=request,
        ) from exc
    await db.commit()
    await _store_idempotent_after_commit(
        redis, result, user_id=user.user_id, settings=settings
    )
    return EventResponse(
        event_id=result.event_id,
        accepted=result.accepted,
        server_received_at=result.server_received_at,
    )


# ============================================================
# /feedback/saved + /feedback/hidden + DELETE /feedback/saved
# ============================================================


async def _document_to_summary(
    db: AsyncSession, document_ids: list[UUID]
) -> dict[UUID, DocumentSummary]:
    """Document → DocumentSummary lookup."""
    if not document_ids:
        return {}
    rows = (
        await db.execute(
            select(
                Document.document_id,
                Document.title,
                Document.url,
                Document.published_at,
                Document.content_type,
                Source.name.label("source_name"),
                Source.source_type.label("source_type"),
            )
            .join(Source, Source.source_id == Document.source_id)
            .where(Document.document_id.in_(document_ids))
        )
    ).all()
    summaries: dict[UUID, DocumentSummary] = {}
    for row in rows:
        # related_topics 는 1차 시연 빈 list (recommendation 응답이 사용).
        try:
            source_type = SourceType(row.source_type)
        except ValueError:
            source_type = SourceType.VENDOR_BLOG
        summaries[row.document_id] = DocumentSummary(
            document_id=row.document_id,
            title=row.title,
            source_name=row.source_name,
            source_type=source_type,
            published_at=row.published_at or datetime.now(UTC),
            url=row.url,
            related_topics=[],
        )
    return summaries


def _parse_cursor(cursor: str | None) -> datetime | None:
    if cursor is None:
        return None
    try:
        return datetime.fromisoformat(cursor)
    except ValueError:
        return None


@router.get(
    "/feedback/saved",
    response_model=PagedResponse[DocumentSummary],
    tags=["feedback"],
    summary="저장 목록 (UI-05)",
)
async def list_saved(
    request: Request,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    cursor: str | None = None,
    limit: int = 20,
) -> PagedResponse[DocumentSummary]:
    cursor_dt = _parse_cursor(cursor)
    limit = max(1, min(limit, 50))
    rows = await interest_service.list_saved_documents(
        db, user.user_id, cursor=cursor_dt, limit=limit
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    summaries = await _document_to_summary(db, [r.document_id for r, _ in rows])
    items = [summaries[r.document_id] for r, _ in rows if r.document_id in summaries]
    next_cursor = rows[-1][1].isoformat() if has_more and rows else None
    return PagedResponse(
        items=items,
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, page_size=limit),
    )


@router.get(
    "/feedback/hidden",
    response_model=PagedResponse[DocumentSummary],
    tags=["feedback"],
    summary="숨김 목록 (UI-05)",
)
async def list_hidden(
    request: Request,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    cursor: str | None = None,
    limit: int = 20,
) -> PagedResponse[DocumentSummary]:
    cursor_dt = _parse_cursor(cursor)
    limit = max(1, min(limit, 50))
    stmt = select(HiddenDocument).where(HiddenDocument.user_id == user.user_id)
    if cursor_dt is not None:
        stmt = stmt.where(HiddenDocument.hidden_at < cursor_dt)
    stmt = stmt.order_by(HiddenDocument.hidden_at.desc()).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    if has_more:
        rows = list(rows)[:limit]
    summaries = await _document_to_summary(db, [r.document_id for r in rows])
    items = [summaries[r.document_id] for r in rows if r.document_id in summaries]
    next_cursor = rows[-1].hidden_at.isoformat() if has_more and rows else None
    return PagedResponse(
        items=items,
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, page_size=limit),
    )


@router.delete(
    "/feedback/saved/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["feedback"],
    summary="저장 해제",
)
async def delete_saved(
    request: Request,
    document_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """동의 비활성이어도 허용 (사용자가 본인 데이터 정리 가능)."""
    await interest_service.delete_saved_document(
        db, user_id=user.user_id, document_id=document_id
    )
    await db.commit()
    await _redis().delete(RedisKey.recommendation_cache(user.user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/feedback/hidden/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["feedback"],
    summary="숨김 해제",
)
async def delete_hidden(
    request: Request,
    document_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    await interest_service.delete_hidden_document(
        db, user_id=user.user_id, document_id=document_id
    )
    await db.commit()
    await _redis().delete(RedisKey.recommendation_cache(user.user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/feedback/not-interested/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["feedback"],
    summary="관심 없음 해제",
)
async def delete_not_interested(
    request: Request,
    document_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    await interest_service.delete_not_interested_for_document(
        db, user_id=user.user_id, document_id=document_id
    )
    await db.commit()
    await _redis().delete(RedisKey.recommendation_cache(user.user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
