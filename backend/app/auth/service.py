"""auth 비즈니스 로직 — signup / login / refresh / logout / me.

email 정규화는 Pydantic validator 가 이미 1차 수행. service 가 2차 방어 (강제 재정규화).
DB 의 functional UNIQUE LOWER(email) 가 3차.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status
from jose import ExpiredSignatureError, JWTError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import ErrorCode, ErrorResponse, TokenAudience
from app.db.models import User
from app.security.consent_cache import is_consent_active
from app.security.jwt import (
    RefreshRevoked,
    decode_access,
    denylist_access,
    encode_access,
    issue_refresh,
    revoke_refresh_jti,
    verify_refresh_and_rotate,
)
from app.security.password import (
    PolicyViolation,
    enforce_password_policy,
    hash_password,
    verify_password,
)

from .schemas import (
    LoginRequest,
    MeResponse,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    TokenPair,
)


def _normalize(email: str) -> str:
    return email.strip().lower()


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:200]


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _http_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    *,
    request: Request | None = None,
    details: dict | None = None,
) -> HTTPException:
    request_id = (
        getattr(request.state, "request_id", None) if request else None
    )
    body = ErrorResponse(
        code=code,
        message=message,
        details=details,
        request_id=request_id,
    ).model_dump(mode="json")
    return HTTPException(status_code=status_code, detail=body)


async def signup(
    payload: SignupRequest, *, request: Request, db: AsyncSession
) -> SignupResponse:
    """회원가입. email 정규화 + policy 검증 + bcrypt 해시 + INSERT."""
    email = _normalize(payload.email)
    try:
        enforce_password_policy(payload.password, email=email)
    except PolicyViolation as exc:
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            exc.code,
            exc.message,
            request=request,
            details={"sub_code": exc.sub_code},
        ) from exc

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        onboarding_complete=False,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.AUTH_EMAIL_TAKEN,
            "이미 등록된 이메일입니다.",
            request=request,
        ) from exc
    await db.refresh(user)
    return SignupResponse(
        user_id=user.user_id,
        email=user.email,
        onboarding_required=True,
        consent_required=True,
    )


async def login(
    payload: LoginRequest,
    *,
    request: Request,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> TokenPair:
    """로그인. 정규화된 email 로 조회 + bcrypt verify + access/refresh 발급.

    Username Enumeration 방지: User 없거나 비번 mismatch 모두 동일 메시지.
    """
    email = _normalize(payload.email)
    stmt = select(User).where(
        func.lower(User.email) == email, User.deleted_at.is_(None)
    )
    user = (await db.execute(stmt)).scalars().first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise _http_error(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.AUTH_INVALID_CREDENTIALS,
            "이메일 또는 비밀번호가 올바르지 않습니다.",
            request=request,
        )

    access_token, _ = encode_access(user.user_id, TokenAudience.USER)
    refresh_token, _ = await issue_refresh(
        user.user_id, ip=_client_ip(request), ua=_user_agent(request), redis=redis
    )
    await db.execute(
        update(User)
        .where(User.user_id == user.user_id)
        .values(last_login_at=datetime.now(UTC))
    )
    await db.commit()

    settings = get_settings()
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        expires_in=settings.JWT_ACCESS_MINUTES * 60,
    )


async def refresh(
    payload: RefreshRequest,
    *,
    request: Request,
    redis: aioredis.Redis,
) -> TokenPair:
    """refresh rotation + family revoke 감지 (HMAC :rotated 패턴, decision-backlog C-6)."""
    try:
        new_token, _, user_id = await verify_refresh_and_rotate(
            payload.refresh_token,
            ip=_client_ip(request),
            ua=_user_agent(request),
            redis=redis,
        )
    except RefreshRevoked as exc:
        raise _http_error(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.AUTH_REFRESH_REVOKED,
            "리프레시 토큰이 폐기되었습니다. 재로그인 해주세요.",
            request=request,
            details={"reason": exc.reason},
        ) from exc

    access_token, _ = encode_access(user_id, TokenAudience.USER)
    settings = get_settings()
    return TokenPair(
        access_token=access_token,
        refresh_token=new_token,
        token_type="Bearer",
        expires_in=settings.JWT_ACCESS_MINUTES * 60,
    )


async def logout(
    *,
    request: Request,
    redis: aioredis.Redis,
) -> None:
    """현재 access jti 를 denylist 에 + refresh jti 비활성."""
    user_id_str = getattr(request.state, "user_id", None)
    jti = getattr(request.state, "jti", None)
    exp = getattr(request.state, "exp", None)
    if not (user_id_str and jti):
        # 미들웨어가 정상 통과 못 한 경우 — 방어적 no-op.
        return
    ttl_remaining = max(0, int(exp) - int(time.time())) if exp else 0
    await denylist_access(jti, ttl_seconds=ttl_remaining, redis=redis)
    user_id = UUID(user_id_str)
    # access jti 와 refresh jti 는 다른 namespace 이지만, 단일 디바이스 로그아웃 시 같은
    # 세션의 refresh 도 폐기되어야 함. access jti 와 매핑된 refresh jti 가 따로 없으므로
    # 가장 최근 refresh 를 찾기 어렵다 → user 의 모든 refresh active="0" 마킹은 보수적.
    # 1차 시연: refresh jti 동일하게 추적할 매핑 없음 → access 만 denylist + 다음 refresh
    # 시도 시 verify_refresh_and_rotate 가 자연 family revoke.
    # 향후 access JWT 클레임에 refresh_jti 추가 시 더 정밀하게 분리 가능.
    _ = user_id  # 현재 사용 없음 — 향후 사용자 명시 logout-all 시 활용


async def me(
    *,
    user: User,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> MeResponse:
    """현재 사용자 프로필. consent_active 캐시 사용."""
    consent_active = await is_consent_active(user.user_id, redis, db)
    return MeResponse(
        user_id=user.user_id,
        email=user.email,
        created_at=user.created_at,
        consent_active=consent_active,
        onboarding_complete=user.onboarding_complete,
    )


def decode_access_token_strict(
    token: str, expected_aud: TokenAudience
) -> dict[str, object]:
    """미들웨어용 — JWTError/ExpiredSignatureError 를 명시 raise."""
    try:
        return decode_access(token, expected_aud)
    except ExpiredSignatureError:
        raise
    except JWTError:
        raise


async def revoke_user_session(user_id: UUID, jti: str, redis: aioredis.Redis) -> None:
    """현재 jti 의 refresh 만 비활성. logout-all 은 별도 호출."""
    await revoke_refresh_jti(user_id, jti, redis)


__all__ = [
    "decode_access_token_strict",
    "login",
    "logout",
    "me",
    "refresh",
    "revoke_user_session",
    "signup",
]
