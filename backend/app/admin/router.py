"""admin router — `/admin/*` 단일 prefix. Phase 0a stub.

`/admin/collection/*` 6 endpoint 는 본 router 단일 SOR (사용자 결정 2026-05-11).
docs: api/admin.md (전체), api/collection.md (§수집 — 사용자용 2 endpoint 만).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.contracts import CollectionJobStatus, PagedResponse

from .schemas import (
    AdminEventView,
    AdminLoginRequest,
    AdminRefreshRequest,
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


# ============================================================
# 인증 (4)
# ============================================================


@router.post(
    "/auth/login",
    response_model=AdminTokenPair,
    summary="관리자 로그인",
)
async def admin_login(req: AdminLoginRequest) -> AdminTokenPair:
    """JWT aud='admin'. 부트스트랩 직후 must_change_password=true."""
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.post(
    "/auth/refresh",
    response_model=AdminTokenPair,
    summary="관리자 토큰 갱신",
)
async def admin_refresh(req: AdminRefreshRequest) -> AdminTokenPair:
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="관리자 로그아웃",
)
async def admin_logout() -> Response:
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.post(
    "/auth/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="관리자 비밀번호 변경 (부트스트랩 시 강제)",
)
async def admin_change_password(req: ChangeAdminPasswordRequest) -> Response:
    raise NotImplementedError("Phase 0b A2에서 구현")


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
async def admin_list_users(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PagedResponse[AdminUserListItem]:
    """role 별 email 마스킹 — operator/read_only 부분 마스킹, super 전체."""
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.get(
    "/users/{user_id}/interest-state",
    response_model=AdminUserInterestState,
    summary="사용자 관심 상태 (점수 포함, 관리자만)",
)
async def admin_user_interest_state(user_id: UUID) -> AdminUserInterestState:
    """NFR-04 우회 — long_score/short_score 노출."""
    raise NotImplementedError("Phase 0b A6에서 구현")


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
