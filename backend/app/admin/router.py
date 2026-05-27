"""admin router — `/admin/*` 단일 prefix.

A2 본문 구현: 인증 4 + 사용자 목록 1 (5 endpoint).
다른 endpoint 는 Phase 0b A3/A4/A5/A6/A8 가 본문 채움 (stub 유지).
`/admin/collection/*` 6 endpoint 는 본 router 단일 SOR (사용자 결정 2026-05-11).
docs: api/admin.md (전체), api/collection.md (§수집 — 사용자용 2 endpoint 만).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.schemas import RunNowResponse
from app.config import get_settings
from app.contracts import CollectionJobStatus, PagedResponse, TraversalStatus
from app.db.models import AdminUser, CSOTopic, DynamicLeafTopic
from app.db.session import get_session
from app.interest import service as interest_service
from app.interest.bucket import bucket_for, bucket_sort_key
from app.interest.config_loader import get_interest_params
from app.redis import get_redis
from app.security.deps import get_current_admin
from app.security.rate_limit import limiter
from app.topic import documents_service, trace_service
from app.topic.schemas import TopicDocumentsResponse, TraversalTraceDetail, TraversalTraceSummary

from . import auth_service, users_service
from .schemas import (
    AdminEventView,
    AdminInterestTopicView,
    AdminLoginRequest,
    AdminLogoutRequest,
    AdminRefreshRequest,
    AdminSignupRequest,
    AdminTokenPair,
    AdminUserInterestState,
    AdminUserListItem,
    ChangeAdminPasswordRequest,
    ClickbaitResultView,
    ClickbaitStatsResponse,
    CollectionJobView,
    CollectionStatsResponse,
    ReprocessRequestPayload,
    ReprocessRequestView,
    SourceTogglePatch,
    SourceView,
    TopicLinkageErrorView,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _redis_default() -> aioredis.Redis:
    return get_redis("default")


_settings = get_settings()


# ============================================================
# 인증 (4) — A2 본문 구현
# ============================================================


@router.post(
    "/auth/signup",
    response_model=AdminTokenPair,
    summary="관리자 회원가입",
)
@limiter.limit(_settings.RATE_LIMIT_SIGNUP)
async def admin_signup_endpoint(
    request: Request,
    req: AdminSignupRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AdminTokenPair:
    return await auth_service.admin_signup(
        req, request=request, db=db, redis=_redis_default()
    )


@router.post(
    "/auth/login",
    response_model=AdminTokenPair,
    summary="관리자 로그인",
)
@limiter.limit(_settings.RATE_LIMIT_LOGIN)
async def admin_login_endpoint(
    request: Request,
    req: AdminLoginRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AdminTokenPair:
    """JWT aud='admin'. 부트스트랩 직후 must_change_password=true."""
    return await auth_service.admin_login(
        req, request=request, db=db, redis=_redis_default()
    )


@router.post(
    "/auth/refresh",
    response_model=AdminTokenPair,
    summary="관리자 토큰 갱신",
)
@limiter.limit("60/hour")
async def admin_refresh_endpoint(
    request: Request,
    req: AdminRefreshRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AdminTokenPair:
    return await auth_service.admin_refresh(
        req, request=request, db=db, redis=_redis_default()
    )


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="관리자 로그아웃",
)
@limiter.limit("30/minute")
async def admin_logout_endpoint(
    request: Request,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    payload: AdminLogoutRequest | None = None,
) -> Response:
    _ = admin  # auth 미들웨어가 이미 jti 셋팅 + get_current_admin 가 must_change_password 예외 처리
    refresh_token = payload.refresh_token if payload else None
    await auth_service.admin_logout(
        request=request, redis=_redis_default(), refresh_token=refresh_token
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/auth/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="관리자 비밀번호 변경 (부트스트랩 시 강제)",
)
@limiter.limit("10/hour")
async def admin_change_password_endpoint(
    request: Request,
    req: ChangeAdminPasswordRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    await auth_service.admin_change_password(
        admin, req, request=request, db=db, redis=_redis_default()
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============================================================
# 수집 (6) — collection.md 의 관리자 영역 단일 SOR
# ============================================================


@router.get(
    "/collection/jobs",
    response_model=PagedResponse[CollectionJobView],
    summary="잡 목록 (필터: status, user_id, source_id, since)",
)
async def admin_list_jobs(
    status_filter: CollectionJobStatus | None = Query(default=None, alias="status"),
    user_id: UUID | None = Query(default=None),
    source_id: UUID | None = Query(default=None),
    since: str | None = Query(default=None, description="ISO8601"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[CollectionJobView]:
    raise NotImplementedError("Phase 0b A4에서 구현")


@router.get(
    "/collection/jobs/{job_id}",
    response_model=CollectionJobView,
    summary="잡 상세 + 실패 로그",
)
async def admin_get_job(job_id: UUID) -> CollectionJobView:
    raise NotImplementedError("Phase 0b A4에서 구현")


@router.post(
    "/collection/jobs/{job_id}/reprocess",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReprocessRequestView,
    summary="재실행 요청 (UC-05, FR-65)",
)
async def admin_reprocess_job(
    job_id: UUID,
    payload: ReprocessRequestPayload,
) -> ReprocessRequestView:
    """이미 큐잉된 동일 잡 → 409 + admin.reprocess_already_queued."""
    raise NotImplementedError("Phase 0b A4에서 구현")


@router.get(
    "/collection/sources",
    response_model=PagedResponse[SourceView],
    summary="소스 레지스트리 + 활성 상태",
)
async def admin_list_sources(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> PagedResponse[SourceView]:
    """api-conventions.md §6 PagedResponse 표준. 소스는 보통 30-80개."""
    raise NotImplementedError("Phase 0b A4에서 구현")


@router.patch(
    "/collection/sources/{source_id}",
    response_model=SourceView,
    summary="소스 활성/비활성 토글",
)
async def admin_toggle_source(
    source_id: UUID,
    patch: SourceTogglePatch,
) -> SourceView:
    raise NotImplementedError("Phase 0b A4에서 구현")


@router.get(
    "/collection/stats",
    response_model=CollectionStatsResponse,
    summary="일일 수집 성공률 + 사용자별 분포 (NFR-10)",
)
async def admin_collection_stats() -> CollectionStatsResponse:
    """success_rate < 0.95 시 alert='below_sla' 응답."""
    raise NotImplementedError("Phase 0b A4에서 구현")


# ============================================================
# 낚시성 통계 (2)
# ============================================================


@router.get(
    "/clickbait/stats",
    response_model=ClickbaitStatsResponse,
    summary="일일 낚시성 통계 (FR-33·63)",
)
async def admin_clickbait_stats() -> ClickbaitStatsResponse:
    """매일 자정 미리 계산 + Redis 24h 캐시."""
    raise NotImplementedError("Phase 0b A5에서 구현")


@router.get(
    "/clickbait/results",
    response_model=PagedResponse[ClickbaitResultView],
    summary="낚시성 판정 결과 목록 (필터링)",
)
async def admin_clickbait_results(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[ClickbaitResultView]:
    raise NotImplementedError("Phase 0b A5에서 구현")


# ============================================================
# 토픽 연결 오류 (2, FR-64)
# ============================================================


@router.get(
    "/topic-linkage/errors",
    response_model=PagedResponse[TopicLinkageErrorView],
    summary="토픽 연결 실패 목록",
)
async def admin_topic_linkage_errors(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[TopicLinkageErrorView]:
    raise NotImplementedError("Phase 0b A3·A8에서 구현")


@router.post(
    "/topic-linkage/errors/{error_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TopicLinkageErrorView,
    summary="토픽 연결 재처리",
)
async def admin_retry_topic_linkage(error_id: UUID) -> TopicLinkageErrorView:
    raise NotImplementedError("Phase 0b A3·A8에서 구현")


# ============================================================
# 사용자 (3)
# ============================================================


@router.get(
    "/users",
    response_model=PagedResponse[AdminUserListItem],
    summary="사용자 목록",
)
@limiter.limit(_settings.RATE_LIMIT_DEFAULT)
async def admin_list_users_endpoint(
    request: Request,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[AdminUserListItem]:
    """role 별 email 마스킹 (decision-backlog C-9): super 전체,
    operator/read_only 는 length≥2 시 `g***d@x`, length=1 시 `***@x`.
    """
    return await users_service.list_users(
        admin,
        cursor=cursor,
        limit=limit,
        db=db,
        redis=_redis_default(),
    )


@router.get(
    "/users/{user_id}/interest-state",
    response_model=AdminUserInterestState,
    summary="사용자 관심 상태 (점수 포함, 관리자만)",
)
async def admin_user_interest_state(
    user_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserInterestState:
    """NFR-04 우회 — long_score/short_score 노출. 관리자 모니터링 도구용 (admin/router-monitor)."""
    _ = admin
    params = await get_interest_params(_redis_default(), db)
    rows = await interest_service.fetch_user_state(db, user_id, limit=50)

    cso_ids = [r.cso_topic_id for r in rows if r.cso_topic_id is not None]
    leaf_ids = [r.leaf_topic_id for r in rows if r.leaf_topic_id is not None]
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

    topics: list[AdminInterestTopicView] = []
    for row in rows:
        bucket = bucket_for(row.long_score, row.short_score, params)
        label = ""
        if row.leaf_topic_id is not None:
            label = leaf_labels.get(row.leaf_topic_id, "")
        elif row.cso_topic_id is not None:
            label = cso_labels.get(row.cso_topic_id, "")
        topics.append(
            AdminInterestTopicView(
                cso_topic_id=row.cso_topic_id,
                leaf_topic_id=row.leaf_topic_id,
                label=label,
                long_score=row.long_score,
                short_score=row.short_score,
                bucket=bucket,
                is_onboarding_selected=row.boost_applied_at_active_day is not None,
            )
        )
    topics.sort(key=lambda t: bucket_sort_key(t.bucket))
    updated_at = await interest_service.fetch_max_updated_at(db, user_id)
    return AdminUserInterestState(
        user_id=user_id,
        topics=topics,
        updated_at=updated_at or datetime.now(timezone.utc),
    )


@router.get(
    "/users/{user_id}/events",
    response_model=PagedResponse[AdminEventView],
    summary="사용자 행동 로그",
)
async def admin_user_events(
    user_id: UUID,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> PagedResponse[AdminEventView]:
    raise NotImplementedError("Phase 0b A6에서 구현")


@router.get(
    "/users/{user_id}/traces",
    response_model=PagedResponse[TraversalTraceSummary],
    summary="사용자 traversal trace 목록 (관리자)",
)
async def admin_user_traces(
    user_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
    status_filter: TraversalStatus | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[TraversalTraceSummary]:
    """관리자 모니터링 — 임의 user_id 의 trace 목록. 본인용 /topics/traces 와 동일 응답."""
    _ = admin
    return await trace_service.list_traces(
        db, user_id, status_filter, cursor, limit
    )


@router.get(
    "/users/{user_id}/traces/{trace_id}",
    response_model=TraversalTraceDetail,
    summary="trace 상세 + 산하 leaf (관리자)",
)
async def admin_user_trace_detail(
    user_id: UUID,
    trace_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TraversalTraceDetail:
    """관리자 모니터링 — 임의 user_id 의 trace 상세 (path + leaves)."""
    _ = admin
    return await trace_service.get_trace_detail(db, user_id, trace_id)


@router.get(
    "/users/{user_id}/topics/{topic_id}/documents",
    response_model=TopicDocumentsResponse,
    summary="사용자별 토픽 수집 문서 (관리자)",
)
async def admin_user_topic_documents(
    user_id: UUID,
    topic_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> TopicDocumentsResponse:
    """관리자 모니터링 — 임의 user_id 의 토픽별 수집 문서. 본인용 /topics/{id}/documents 와 동일."""
    _ = admin
    return await documents_service.list_topic_documents(
        db, user_id, topic_id, since, cursor, limit
    )


@router.post(
    "/users/{user_id}/collection/run-now",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunNowResponse,
    summary="사용자 문서 수집 즉시 실행",
)
@limiter.limit("10/hour")
async def admin_run_user_collection_now(
    request: Request,
    user_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> RunNowResponse:
    """관리자 콘솔에서 동의 활성 사용자 1명의 collection job을 큐잉한다."""
    _ = request
    return await users_service.trigger_user_collection_now(
        admin,
        user_id=user_id,
        db=db,
        redis=_redis_default(),
    )


@router.post(
    "/cron/user-profile/trigger",
    status_code=status.HTTP_202_ACCEPTED,
    summary="UserProfile daily cron 즉시 실행 (C-62 후속)",
)
@limiter.limit("5/hour")
async def admin_trigger_user_profile_cron(
    request: Request,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
) -> dict[str, str]:
    """(C-62 후속, 2026-05-26) 관리자가 모든 사용자의 UserProfile cron 을 RQ 큐잉.

    Discovery slot 의 Fusion/Reincarnation 가 UserProfile 의 fusion_candidates /
    broadening_seeds / deepening_seeds 에 의존하므로, 데모 환경에서 daily 19 UTC cron
    안 돌았으면 본 endpoint 로 수동 트리거. 사용자 전원 순회.

    Args: admin (super 권한). rate_limit 5/hour (LLM 호출 비용 가드).
    """
    _ = request, admin
    import redis as sync_redis
    from rq import Queue

    settings = get_settings()
    sync_conn = sync_redis.Redis.from_url(settings.REDIS_URL_QUEUE)
    queue = Queue("default", connection=sync_conn)
    rq_job = queue.enqueue(
        "app.worker.jobs.user_profile.user_profile_generation_job",
        job_timeout=7200,
        failure_ttl=86_400,
        result_ttl=3_600,
    )
    return {"status": "queued", "rq_job_id": str(rq_job.id)}


# ============================================================
# 재실행 요청 이력 (2)
# ============================================================


@router.get(
    "/reprocess-requests",
    response_model=PagedResponse[ReprocessRequestView],
    summary="재실행 요청 목록",
)
async def admin_list_reprocess_requests(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[ReprocessRequestView]:
    raise NotImplementedError("Phase 0b A4에서 구현")


@router.get(
    "/reprocess-requests/{request_id}",
    response_model=ReprocessRequestView,
    summary="재실행 요청 단건",
)
async def admin_get_reprocess_request(request_id: UUID) -> ReprocessRequestView:
    raise NotImplementedError("Phase 0b A4에서 구현")
