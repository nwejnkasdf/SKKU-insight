"""onboarding 비즈니스 로직 — POST/PUT /interests + GET /cold-start-status.

POST:
1) consent active 검증 (`onboarding.consent_required`)
2) cluster_ids 비검사 (`onboarding.no_cluster_selected`)
3) BroadInterest IN 검증 (`onboarding.invalid_cluster`)
4) X-Idempotency-Key 캐시 hit 시 기존 응답 반환
5) Redis `lock:onboarding:{user_id}` NX EX=30 — 실패 시 진행 중 request_id 반환
6) `cold_start:status:{request_id}` HSET queued + User.onboarding_complete=true
7) RQ enqueue `cold_start_job` (worker function 본문은 A8)
8) Prefer: respond=sync 헤더 시 8s 폴링 (asyncio.sleep(0.5) x 16)

PUT: 1차는 onboarding_complete 유지 + 202 reply. prior boost 갱신은 A6, stale 은 A7.

GET status: Redis HGETALL `cold_start:status:{request_id}` → ColdStartStatusResponse.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import ErrorCode, ErrorResponse, RedisKey
from app.db.models import BroadInterest, User
from app.security.consent_cache import is_consent_active
from app.security.idempotency import (
    lookup_idempotent_response,
    store_idempotent_response,
)

from .schemas import (
    ColdStartStatusResponse,
    OnboardingInterestsRequest,
    OnboardingInterestsResponse,
)

ONBOARDING_LOCK_TTL = 30
COLD_START_STATUS_TTL = 3600
PREFER_SYNC_TIMEOUT_SECONDS = 8
PREFER_SYNC_POLL_INTERVAL = 0.5
ESTIMATED_COLD_START_SECONDS = 8


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


def _build_response(
    request_id: UUID, status_value: str = "queued"
) -> OnboardingInterestsResponse:
    return OnboardingInterestsResponse(
        request_id=request_id,
        status="queued" if status_value == "queued" else "completed",
        polling_url=f"/onboarding/cold-start-status/{request_id}",
        estimated_seconds=ESTIMATED_COLD_START_SECONDS,
    )


async def post_interests(
    user: User,
    payload: OnboardingInterestsRequest,
    *,
    request: Request,
    db: AsyncSession,
    redis: aioredis.Redis,
    idempotency_key: str | None,
    prefer_sync: bool,
) -> OnboardingInterestsResponse:
    """클러스터 선택 + cold-start 트리거."""
    # 1) consent active
    if not await is_consent_active(user.user_id, redis, db):
        raise _http_error(
            status.HTTP_403_FORBIDDEN,
            ErrorCode.ONBOARDING_CONSENT_REQUIRED,
            "온보딩 전에 개인화 동의가 필요합니다.",
            request=request,
        )
    # 2) 비검사
    if not payload.cso_cluster_ids:
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCode.ONBOARDING_NO_CLUSTER_SELECTED,
            "최소 1개의 관심 클러스터를 선택해야 합니다.",
            request=request,
        )
    # 3) BroadInterest 존재 검증
    cluster_ids = await _valid_cluster_ids(payload.cso_cluster_ids, db)
    if len(cluster_ids) != len(payload.cso_cluster_ids):
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCode.ONBOARDING_INVALID_CLUSTER,
            "선택한 클러스터 중 일부가 유효하지 않습니다.",
            request=request,
            details={"invalid_count": len(payload.cso_cluster_ids) - len(cluster_ids)},
        )
    # 4) Idempotency-Key 캐시
    cached = await lookup_idempotent_response(
        "onboarding", user.user_id, idempotency_key, redis
    )
    if cached is not None:
        return OnboardingInterestsResponse.model_validate(cached)
    # 5) Single-flight lock
    lock_key = RedisKey.onboarding_lock(user.user_id)
    locked = await redis.set(lock_key, "1", nx=True, ex=ONBOARDING_LOCK_TTL)
    if not locked:
        # 진행 중 request_id 반환 (idempotent)
        existing_request_id = await redis.get(f"{lock_key}:request_id")
        if existing_request_id:
            try:
                return _build_response(UUID(existing_request_id))
            except ValueError:
                pass
        raise _http_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.ONBOARDING_ALREADY_IN_PROGRESS,
            "이미 온보딩이 진행 중입니다. 잠시 후 다시 시도해주세요.",
            request=request,
        )

    request_id = uuid4()
    # codex v2 #3 → C-23: 기존 finally 가 lock 즉시 삭제 → cold-start job 큐잉 중
    # 동일 사용자 재호출 시 중복 enqueue. 본 패치는 lock 을 TTL 30s 자연 만료에 위임
    # — cold-start orchestrator (A8) 가 완료 시 명시 DEL 추가 가능. lock 키가 살아
    # 있는 동안 다음 POST 는 진행 중 request_id 회귀 응답.
    await redis.set(f"{lock_key}:request_id", str(request_id), ex=ONBOARDING_LOCK_TTL)
    await redis.hset(  # type: ignore[misc]
        RedisKey.cold_start_status(request_id),
        mapping={
            "status": "queued",
            "progress_percent": "0",
            "dashboard_ready": "false",
        },
    )
    await redis.expire(
        RedisKey.cold_start_status(request_id), COLD_START_STATUS_TTL
    )
    await db.execute(
        update(User).where(User.user_id == user.user_id).values(onboarding_complete=True)
    )
    # A6 협업: 12 cluster + 1-hop child UserInterestState row prefilled (alpha_prior+boost,
    # boost_applied_at_active_day = user.active_day_counter). 14-day decay cron 이 자연 만료.
    # cso_graph 는 lifespan startup 에서 app.state.cso_graph 에 binding 됨.
    #
    # Codex S-06 fix: bootstrap 중간 실패 시 partial INSERT row 만 rollback 하도록
    # savepoint 로 격리. begin_nested() 사용 — 실패 시 savepoint 만 rollback 하고
    # outer 트랜잭션 (User.onboarding_complete=true 등) 은 commit 유지.
    cso_graph = getattr(request.app.state, "cso_graph", None)
    if cso_graph is not None:
        from app.interest.service import bootstrap_interest_state

        savepoint = await db.begin_nested()
        try:
            await bootstrap_interest_state(
                db,
                cso_graph,
                user=user,
                cluster_ids=cluster_ids,
                active_day=user.active_day_counter,
                redis=redis,
            )
        except Exception as exc:
            # boost 시드 실패는 onboarding 자체를 막지 않음 — savepoint rollback 으로
            # partial INSERT row 제거 + A6 decay cron / 첫 이벤트 시 lazy 시드로 회복.
            await savepoint.rollback()
            import structlog as _structlog

            _structlog.get_logger("onboarding").warning(
                "bootstrap_interest_state failed (savepoint rolled back)",
                user_id=str(user.user_id),
                error=str(exc),
            )
        else:
            await savepoint.commit()
    await db.commit()
    _enqueue_cold_start_job(
        request_id=request_id,
        user_id=user.user_id,
        cluster_ids=cluster_ids,
        user_class=payload.user_class.value,
        locale=payload.locale,
    )

    # Prefer: respond=sync 8s 폴링
    if prefer_sync:
        for _ in range(int(PREFER_SYNC_TIMEOUT_SECONDS / PREFER_SYNC_POLL_INTERVAL)):
            await asyncio.sleep(PREFER_SYNC_POLL_INTERVAL)
            status_value = await redis.hget(  # type: ignore[misc]
                RedisKey.cold_start_status(request_id), "status"
            )
            if status_value in ("completed", "failed"):
                response = _build_response(request_id, status_value)
                await store_idempotent_response(
                    "onboarding",
                    user.user_id,
                    idempotency_key,
                    response.model_dump(mode="json"),
                    redis,
                )
                return response

    response = _build_response(request_id, "queued")
    await store_idempotent_response(
        "onboarding",
        user.user_id,
        idempotency_key,
        response.model_dump(mode="json"),
        redis,
    )
    return response


async def put_interests(
    user: User,
    payload: OnboardingInterestsRequest,
    *,
    request: Request,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> OnboardingInterestsResponse:
    """FR-55 설정 화면 — 관심 분야 수정.

    A2 1차 시연: cluster 유효성 검증 + onboarding_complete 유지 + 202 reply 만.
    prior boost 갱신은 A6 (interest-bayesian), stale 마킹은 A7 (leaf-traversal).
    """
    if not payload.cso_cluster_ids:
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCode.ONBOARDING_NO_CLUSTER_SELECTED,
            "최소 1개의 관심 클러스터를 선택해야 합니다.",
            request=request,
        )
    valid_ids = await _valid_cluster_ids(payload.cso_cluster_ids, db)
    if len(valid_ids) != len(payload.cso_cluster_ids):
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCode.ONBOARDING_INVALID_CLUSTER,
            "선택한 클러스터 중 일부가 유효하지 않습니다.",
            request=request,
        )
    _ = user, db, redis  # 1차는 변경 없음 — onboarding_complete 는 이미 true
    request_id = uuid4()
    return _build_response(request_id, "queued")


async def get_cold_start_status(
    request_id: UUID, *, request: Request, redis: aioredis.Redis
) -> ColdStartStatusResponse:
    """Redis 키 HGETALL → ColdStartStatusResponse."""
    raw = await redis.hgetall(RedisKey.cold_start_status(request_id))  # type: ignore[misc]
    if not raw:
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            ErrorCode.VALIDATION_ERROR,
            "요청 ID를 찾을 수 없습니다.",
            request=request,
        )
    completed_at_raw = raw.get("completed_at")
    completed_at: datetime | None = None
    if completed_at_raw:
        try:
            completed_at = datetime.fromisoformat(completed_at_raw)
        except ValueError:
            completed_at = None
    status_value = raw.get("status", "queued")
    if status_value not in ("queued", "running", "completed", "failed"):
        status_value = "queued"
    return ColdStartStatusResponse(
        request_id=request_id,
        status=status_value,
        progress_percent=int(raw.get("progress_percent", "0") or "0"),
        completed_at=completed_at,
        dashboard_ready=raw.get("dashboard_ready", "false") == "true",
        error_code=raw.get("error_code") or None,
    )


async def _valid_cluster_ids(
    candidates: list[UUID], db: AsyncSession
) -> list[UUID]:
    if not candidates:
        return []
    stmt = select(
        BroadInterest.broad_interest_id,
        BroadInterest.cso_seed_topic_id,
    ).where(
        or_(
            BroadInterest.broad_interest_id.in_(candidates),
            BroadInterest.cso_seed_topic_id.in_(candidates),
        )
    )
    rows = (await db.execute(stmt)).all()
    candidate_to_broad_id: dict[UUID, UUID] = {}
    for row in rows:
        candidate_to_broad_id[row.broad_interest_id] = row.broad_interest_id
        candidate_to_broad_id[row.cso_seed_topic_id] = row.broad_interest_id
    return [
        candidate_to_broad_id[candidate]
        for candidate in candidates
        if candidate in candidate_to_broad_id
    ]


def _enqueue_cold_start_job(
    *,
    request_id: UUID,
    user_id: UUID,
    cluster_ids: list[UUID],
    user_class: str,
    locale: str,
) -> None:
    """RQ enqueue. worker function 본문은 backend/app/worker/jobs/cold_start.py
    (현재 A8 stub — NotImplementedError)."""
    import redis as sync_redis
    from rq import Queue

    from app.config import get_settings

    settings = get_settings()
    sync_conn = sync_redis.Redis.from_url(settings.REDIS_URL_QUEUE)
    queue = Queue("default", connection=sync_conn)
    queue.enqueue(
        "app.worker.jobs.cold_start.cold_start_job",
        args=(
            str(request_id),
            str(user_id),
            [str(c) for c in cluster_ids],
            user_class,
            locale,
        ),
        job_timeout=180,
        failure_ttl=86_400,
        result_ttl=COLD_START_STATUS_TTL,
    )


__all__ = [
    "get_cold_start_status",
    "post_interests",
    "put_interests",
]
