"""consent router — Phase 0b A2 본문.

4 endpoint: GET / POST / /revoke / /account-deletion.
docs: api/consent.md.
"""
from __future__ import annotations

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.session import get_session
from app.redis import get_redis
from app.security.deps import get_current_user
from app.security.rate_limit import limiter

from . import service
from .schemas import (
    AccountDeletionRequest,
    AccountDeletionResponse,
    ConsentRequest,
    ConsentRevokeRequest,
    ConsentStateResponse,
)

router = APIRouter(prefix="/consent", tags=["consent"])


def _redis_default() -> aioredis.Redis:
    return get_redis("default")


def _redis_queue() -> aioredis.Redis:
    return get_redis("queue")


_settings = get_settings()


@router.get(
    "",
    response_model=ConsentStateResponse,
    summary="자기 동의 상태 조회 (FR-05·06)",
)
@limiter.limit(_settings.RATE_LIMIT_DEFAULT)
async def get_consent_state_endpoint(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentStateResponse:
    return await service.get_state(user, db)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ConsentStateResponse,
    summary="동의 등록·갱신 (FR-05·11)",
)
@limiter.limit("10/minute")
async def post_consent_endpoint(
    request: Request,
    req: ConsentRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentStateResponse:
    return await service.register(
        user, req, request=request, db=db, redis=_redis_default()
    )


@router.post(
    "/revoke",
    response_model=ConsentStateResponse,
    summary="동의 철회 (FR-58·59)",
)
@limiter.limit(_settings.RATE_LIMIT_REVOKE_CONSENT)
async def revoke_consent_endpoint(
    request: Request,
    req: ConsentRevokeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentStateResponse:
    """철회 후 추천 캐시 폐기 + consent cache invalidate (FR-59)."""
    return await service.revoke(
        user, req, request=request, db=db, redis=_redis_default()
    )


@router.post(
    "/account-deletion",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AccountDeletionResponse,
    summary="계정·개인화 데이터 삭제 (FR-56)",
)
@limiter.limit(_settings.RATE_LIMIT_DELETE_ACCOUNT)
async def request_account_deletion_endpoint(
    request: Request,
    req: AccountDeletionRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AccountDeletionResponse:
    """RQ async + worker. expected_deletion_by = now()+5분 (C-2 부분 해소, A2 결정)."""
    return await service.request_account_deletion(
        user,
        req,
        request=request,
        db=db,
        redis=_redis_default(),
        queue_redis=_redis_queue(),
    )
