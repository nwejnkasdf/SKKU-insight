"""collection router — 사용자용만. 관리자 영역(`/admin/collection/*`)은 admin 모듈.

docs: api/collection.md (§사용자), api/admin.md (§수집).
v13 라운드 A4 본문 구현 — service 위임 + rate limit + lock 확인 + RQ enqueue.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.session import get_session
from app.redis import get_redis
from app.security.deps import get_current_user
from app.security.rate_limit import limiter

from . import service
from .schemas import CollectionJobMeResponse, RunNowResponse

router = APIRouter(prefix="/collection", tags=["collection"])

_settings = get_settings()


@router.get(
    "/jobs/me",
    response_model=CollectionJobMeResponse,
    summary="자기 최근 수집 잡 상태",
)
async def get_my_jobs(
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> CollectionJobMeResponse:
    """7일 윈도우. latest + history. NFR-08 — failure_reason 마스킹."""
    return await service.get_my_jobs(db, user.user_id, cursor, limit)


@router.post(
    "/jobs/me/run-now",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunNowResponse,
    summary="강제 트리거 (시연용, 1/시간/사용자)",
)
@limiter.limit(_settings.RATE_LIMIT_RUN_NOW)
async def run_my_collection_now(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> RunNowResponse:
    """rate limit 1/hour. 동일 사용자 잡 진행 중이면 409 + collection.already_running."""
    return await service.trigger_run_now(db, get_redis("default"), user.user_id)
