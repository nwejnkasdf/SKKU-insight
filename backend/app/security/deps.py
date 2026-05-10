"""FastAPI Depends — get_current_user / get_current_admin / require_consent_active.

JWT 인증 미들웨어가 request.state.user_id / aud / jti 를 셋팅한 후 본 Depends 가
DB 에서 User/AdminUser 로드 + 추가 검증.
"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import AdminRole, ErrorCode, ErrorResponse, TokenAudience
from app.db.models import AdminUser, User
from app.db.session import get_session
from app.redis import get_redis
from app.security.consent_cache import is_consent_active


def _redis_default() -> aioredis.Redis:
    """기본 DB Redis client."""
    return get_redis("default")


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """JWT 미들웨어가 셋팅한 user_id 로 User 로드. aud=user 강제."""
    user_id_str = getattr(request.state, "user_id", None)
    aud = getattr(request.state, "aud", None)
    if not user_id_str or aud != TokenAudience.USER.value:
        raise _http_error(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.AUTH_INVALID_TOKEN,
            "유효하지 않은 토큰입니다.",
            request_id=getattr(request.state, "request_id", None),
        )
    user_id = UUID(user_id_str)
    stmt = select(User).where(User.user_id == user_id, User.deleted_at.is_(None))
    user = (await db.execute(stmt)).scalars().first()
    if user is None:
        raise _http_error(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.AUTH_INVALID_TOKEN,
            "유효하지 않은 토큰입니다.",
            request_id=getattr(request.state, "request_id", None),
        )
    return user


async def get_current_admin(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUser:
    """JWT 미들웨어가 셋팅한 admin_id 로 AdminUser 로드. aud=admin 강제."""
    admin_id_str = getattr(request.state, "user_id", None)
    aud = getattr(request.state, "aud", None)
    if not admin_id_str or aud != TokenAudience.ADMIN.value:
        raise _http_error(
            status.HTTP_403_FORBIDDEN,
            ErrorCode.ADMIN_UNAUTHORIZED,
            "관리자 권한이 필요합니다.",
            request_id=getattr(request.state, "request_id", None),
        )
    admin_id = UUID(admin_id_str)
    stmt = select(AdminUser).where(
        AdminUser.admin_id == admin_id, AdminUser.status == "active"
    )
    admin = (await db.execute(stmt)).scalars().first()
    if admin is None:
        raise _http_error(
            status.HTTP_403_FORBIDDEN,
            ErrorCode.ADMIN_UNAUTHORIZED,
            "관리자 권한이 필요합니다.",
            request_id=getattr(request.state, "request_id", None),
        )
    # codex C-4: 부트스트랩 admin 의 must_change_password 강제 차단.
    # 예외 경로: 비번 변경(/admin/auth/change-password) + 로그아웃(/admin/auth/logout).
    # admin-bootstrap.md 가 다른 admin API 호출을 409 admin.must_change_password 로 막도록 명시.
    if admin.must_change_password:
        path = request.url.path.rstrip("/")
        if path not in (
            "/admin/auth/change-password",
            "/admin/auth/logout",
        ):
            raise _http_error(
                status.HTTP_409_CONFLICT,
                ErrorCode.ADMIN_MUST_CHANGE_PASSWORD,
                "관리자 첫 로그인 후 비밀번호를 변경해야 합니다.",
                request_id=getattr(request.state, "request_id", None),
            )
    return admin


def require_admin_role(*allowed: AdminRole):  # type: ignore[no-untyped-def]
    """role 별 권한 게이트. 예: Depends(require_admin_role(AdminRole.SUPER, AdminRole.OPERATOR))."""
    allowed_values = {r.value for r in allowed}

    async def _guard(
        admin: Annotated[AdminUser, Depends(get_current_admin)],
        request: Request,
    ) -> AdminUser:
        if admin.role not in allowed_values:
            raise _http_error(
                status.HTTP_403_FORBIDDEN,
                ErrorCode.ADMIN_ROLE_INSUFFICIENT,
                "권한이 부족합니다.",
                request_id=getattr(request.state, "request_id", None),
            )
        return admin

    return _guard


async def require_consent_active(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """personalization endpoint 의 consent 게이트."""
    redis = _redis_default()
    if not await is_consent_active(user.user_id, redis, db):
        raise _http_error(
            status.HTTP_403_FORBIDDEN,
            ErrorCode.CONSENT_REQUIRED,
            "개인화 동의가 필요합니다.",
            request_id=getattr(request.state, "request_id", None),
            details={"reauth_required": True},
        )
    return user


def _http_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    *,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> HTTPException:
    """ErrorResponse 형식 HTTPException 생성."""
    body = ErrorResponse(
        code=code,
        message=message,
        details=details,
        request_id=request_id,
    ).model_dump(mode="json")
    return HTTPException(status_code=status_code, detail=body)


__all__ = [
    "get_current_admin",
    "get_current_user",
    "require_admin_role",
    "require_consent_active",
]
