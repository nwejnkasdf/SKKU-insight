"""admin 인증 비즈니스 — login / refresh / logout / change-password.

JWT aud=admin. must_change_password 필드는 부트스트랩 직후 true → 강제 비번 변경.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import ErrorCode, ErrorResponse, TokenAudience
from app.db.models import AdminUser
from app.security.jwt import (
    RefreshRevoked,
    denylist_access,
    encode_access,
    issue_refresh,
    revoke_all_user_refresh,
    verify_refresh_and_rotate,
)
from app.security.password import (
    PolicyViolation,
    enforce_password_policy,
    hash_password,
    verify_password,
)

from .schemas import (
    AdminLoginRequest,
    AdminRefreshRequest,
    AdminTokenPair,
    ChangeAdminPasswordRequest,
)


def _normalize(email: str) -> str:
    return email.strip().lower()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:200]


def _http_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    *,
    request: Request,
    details: dict | None = None,
) -> HTTPException:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        code=code, message=message, details=details, request_id=request_id
    ).model_dump(mode="json")
    return HTTPException(status_code=status_code, detail=body)


async def admin_login(
    payload: AdminLoginRequest,
    *,
    request: Request,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> AdminTokenPair:
    email = _normalize(payload.email)
    stmt = select(AdminUser).where(
        func.lower(AdminUser.email) == email, AdminUser.status == "active"
    )
    admin = (await db.execute(stmt)).scalars().first()
    if admin is None or not verify_password(payload.password, admin.password_hash):
        raise _http_error(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.AUTH_INVALID_CREDENTIALS,
            "이메일 또는 비밀번호가 올바르지 않습니다.",
            request=request,
        )

    access_token, _ = encode_access(admin.admin_id, TokenAudience.ADMIN)
    refresh_token, _ = await issue_refresh(
        admin.admin_id, ip=_client_ip(request), ua=_user_agent(request), redis=redis
    )
    await db.execute(
        update(AdminUser)
        .where(AdminUser.admin_id == admin.admin_id)
        .values(last_login_at=datetime.now(UTC))
    )
    await db.commit()

    settings = get_settings()
    return AdminTokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_MINUTES * 60,
        must_change_password=admin.must_change_password,
    )


async def admin_refresh(
    payload: AdminRefreshRequest,
    *,
    request: Request,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> AdminTokenPair:
    try:
        new_token, _, admin_id = await verify_refresh_and_rotate(
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

    stmt = select(AdminUser).where(
        AdminUser.admin_id == admin_id, AdminUser.status == "active"
    )
    admin = (await db.execute(stmt)).scalars().first()
    if admin is None:
        raise _http_error(
            status.HTTP_403_FORBIDDEN,
            ErrorCode.ADMIN_UNAUTHORIZED,
            "관리자 권한이 없습니다.",
            request=request,
        )

    access_token, _ = encode_access(admin.admin_id, TokenAudience.ADMIN)
    settings = get_settings()
    return AdminTokenPair(
        access_token=access_token,
        refresh_token=new_token,
        expires_in=settings.JWT_ACCESS_MINUTES * 60,
        must_change_password=admin.must_change_password,
    )


async def admin_logout(
    *,
    request: Request,
    redis: aioredis.Redis,
) -> None:
    jti = getattr(request.state, "jti", None)
    exp = getattr(request.state, "exp", None)
    if jti:
        ttl_remaining = max(0, int(exp) - int(time.time())) if exp else 0
        await denylist_access(jti, ttl_seconds=ttl_remaining, redis=redis)


async def admin_change_password(
    admin: AdminUser,
    payload: ChangeAdminPasswordRequest,
    *,
    request: Request,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> None:
    if not verify_password(payload.current_password, admin.password_hash):
        raise _http_error(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.AUTH_INVALID_CREDENTIALS,
            "현재 비밀번호가 올바르지 않습니다.",
            request=request,
        )
    try:
        enforce_password_policy(payload.new_password, email=admin.email)
    except PolicyViolation as exc:
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            exc.code,
            exc.message,
            request=request,
            details={"sub_code": exc.sub_code},
        ) from exc

    await db.execute(
        update(AdminUser)
        .where(AdminUser.admin_id == admin.admin_id)
        .values(
            password_hash=hash_password(payload.new_password),
            must_change_password=False,
        )
    )
    await db.commit()
    # 비번 변경 시 모든 admin refresh 세션 폐기
    await revoke_all_user_refresh(admin.admin_id, redis)


__all__ = [
    "admin_change_password",
    "admin_login",
    "admin_logout",
    "admin_refresh",
]
