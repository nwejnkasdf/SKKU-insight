"""auth router — Phase 0a stub.

모든 endpoint body 는 NotImplementedError. Phase 0b A2 가 본문 구현.
docs: api/auth.md, security/auth-flow.md, security/token-handling.md, security/password-policy.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Response, status

from .schemas import (
    LoginRequest,
    MeResponse,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=SignupResponse,
    summary="회원가입 (FR-01)",
)
async def signup(req: SignupRequest) -> SignupResponse:
    """회원가입. NFR-15·16·17, password-policy.md 룰 적용."""
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.post(
    "/login",
    response_model=TokenPair,
    summary="로그인 (FR-02)",
)
async def login(req: LoginRequest) -> TokenPair:
    """이메일·비밀번호 로그인. rate limit 5/분/IP."""
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="액세스 토큰 갱신",
)
async def refresh(req: RefreshRequest) -> TokenPair:
    """refresh token 으로 새 access token 발급. token-handling.md §refresh."""
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="로그아웃",
)
async def logout() -> Response:
    """refresh token 폐기 + access token jti deny-list (15m TTL)."""
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.get(
    "/me",
    response_model=MeResponse,
    summary="자기 정보 조회",
)
async def me() -> MeResponse:
    """현재 사용자 프로필. consent_active + onboarding_complete 포함."""
    raise NotImplementedError("Phase 0b A2에서 구현")
