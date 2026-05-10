"""collection router — 사용자용만. 관리자 영역(`/admin/collection/*`)은 admin 모듈.

docs: api/collection.md (§사용자), api/admin.md (§수집).
"""
from __future__ import annotations

from fastapi import APIRouter, status

from .schemas import CollectionJobMeResponse, RunNowResponse

router = APIRouter(prefix="/collection", tags=["collection"])


@router.get(
    "/jobs/me",
    response_model=CollectionJobMeResponse,
    summary="자기 최근 수집 잡 상태",
)
async def get_my_jobs() -> CollectionJobMeResponse:
    raise NotImplementedError("Phase 0b A4에서 구현")


@router.post(
    "/jobs/me/run-now",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunNowResponse,
    summary="강제 트리거 (시연용, 1/시간/사용자)",
)
async def run_my_collection_now() -> RunNowResponse:
    """rate limit 1/hour. 동일 사용자 잡 진행 중이면 409 + collection.already_running."""
    raise NotImplementedError("Phase 0b A4에서 구현")
