"""auth router — Phase 0b A2 본문.

5 endpoint: signup / login / refresh / logout / me.
rate limit 은 docs/security/rate-limiting.md + env-vars.md 정확값.
"""
from __future__ import annotations

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.session import get_session
from app.redis import get_redis
from app.security.deps import get_current_user
from app.security.rate_limit import limiter

from . import service
from .schemas import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _redis_default() -> aioredis.Redis:
    return get_redis("default")


_settings = get_settings()


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=SignupResponse,
    summary="회원가입 (FR-01)",
)
@limiter.limit(_settings.RATE_LIMIT_SIGNUP)
async def signup_endpoint(
    request: Request,
    req: SignupRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SignupResponse:
    """회원가입. NFR-15·16·17 + password-policy.md + email 정규화 3겹."""
    return await service.signup(req, request=request, db=db)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="로그인 (FR-02)",
)
@limiter.limit(_settings.RATE_LIMIT_LOGIN)
async def login_endpoint(
    request: Request,
    req: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    """이메일·비밀번호 로그인. rate limit 5/분/IP."""
    return await service.login(req, request=request, db=db, redis=_redis_default())


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="액세스 토큰 갱신",
)
@limiter.limit("60/hour")
async def refresh_endpoint(
    request: Request,
    req: RefreshRequest,
) -> TokenPair:
    """refresh rotation. HMAC :rotated 마커 family revoke (decision-backlog C-6)."""
    return await service.refresh(req, request=request, redis=_redis_default())


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="로그아웃",
)
@limiter.limit("30/minute")
async def logout_endpoint(
    request: Request,
    payload: LogoutRequest | None = None,
) -> Response:
    """access jti denylist + body 의 refresh token 도 함께 폐기 (codex C-2)."""
    await service.logout(payload, request=request, redis=_redis_default())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="자기 정보 조회",
)
@limiter.limit(_settings.RATE_LIMIT_DEFAULT)
async def me_endpoint(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    """현재 사용자 프로필. consent_active + onboarding_complete 포함."""
    return await service.me(user=user, db=db, redis=_redis_default())
