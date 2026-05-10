"""JWT 인증 미들웨어 — Bearer 검증 + aud + denylist.

화이트리스트 (인증 우회):
- /health, /docs, /openapi.json, /redoc
- /auth/signup, /auth/login, /auth/refresh
- /admin/auth/login

그 외 모든 경로는 `Authorization: Bearer <token>` 헤더 강제. 검증 성공 시
request.state.user_id / aud / jti / exp 셋팅. 실패 시 401 + WWW-Authenticate.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from jose import ExpiredSignatureError, JWTError
from starlette.middleware.base import BaseHTTPMiddleware

from app.contracts import ErrorCode, ErrorResponse, RedisKey, TokenAudience
from app.redis import get_redis
from app.security.jwt import decode_access, is_jti_denylisted

WHITELIST_PATTERNS = [
    re.compile(r"^/health/?$"),
    re.compile(r"^/docs/?$"),
    re.compile(r"^/redoc/?$"),
    re.compile(r"^/openapi\.json$"),
    re.compile(r"^/auth/signup/?$"),
    re.compile(r"^/auth/login/?$"),
    re.compile(r"^/auth/refresh/?$"),
    re.compile(r"^/admin/auth/login/?$"),
    # codex C-3: admin refresh 도 access 만료 후 호출되므로 인증 우회 대상.
    re.compile(r"^/admin/auth/refresh/?$"),
]


class JwtAuthMiddleware(BaseHTTPMiddleware):
    """Bearer 강제 + aud 검증 + denylist 체크."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or _is_whitelisted(path):
            return await call_next(request)

        authz = request.headers.get("Authorization", "")
        if not authz.startswith("Bearer "):
            return _unauthorized(
                request, ErrorCode.AUTH_INVALID_TOKEN, "인증 토큰이 필요합니다."
            )
        token = authz[len("Bearer ") :].strip()
        expected_aud = (
            TokenAudience.ADMIN
            if path.startswith("/admin/")
            else TokenAudience.USER
        )
        try:
            payload = decode_access(token, expected_aud)
        except ExpiredSignatureError:
            return _unauthorized(
                request, ErrorCode.AUTH_TOKEN_EXPIRED, "토큰이 만료되었습니다."
            )
        except JWTError:
            return _unauthorized(
                request,
                ErrorCode.AUTH_INVALID_TOKEN,
                "유효하지 않은 토큰입니다.",
            )

        jti = str(payload.get("jti", ""))
        redis = get_redis("default")
        if jti and await is_jti_denylisted(jti, redis):
            return _unauthorized(
                request,
                ErrorCode.AUTH_INVALID_TOKEN,
                "토큰이 폐기되었습니다.",
            )

        sub_str = str(payload.get("sub", ""))
        # codex v2 #2 → C-22: account deletion 진행 중인 user 의 access 차단.
        # endpoint 가 202 반환 후 worker 가 row delete 하기 전까지 access TTL (15m)
        # 동안 유효해 personalization API 호출 가능했음 — 본 gate 가 차단.
        # /admin/* 은 별도 권한 흐름이므로 user audience 만 검사.
        if sub_str and expected_aud == TokenAudience.USER:
            try:
                user_uuid = UUID(sub_str)
                if await redis.exists(
                    RedisKey.account_deletion_pending(user_uuid)
                ):
                    return _unauthorized(
                        request,
                        ErrorCode.CONSENT_DELETION_IN_PROGRESS,
                        "계정 삭제가 진행 중입니다. 모든 세션이 곧 종료됩니다.",
                    )
            except ValueError:
                pass

        request.state.user_id = sub_str
        request.state.aud = str(payload.get("aud", ""))
        request.state.jti = jti
        request.state.exp = payload.get("exp")
        return await call_next(request)


def _is_whitelisted(path: str) -> bool:
    return any(p.match(path) for p in WHITELIST_PATTERNS)


def _unauthorized(
    request: Request, code: ErrorCode, message: str
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        code=code, message=message, request_id=request_id
    ).model_dump(mode="json")
    response = JSONResponse(status_code=401, content=body)
    response.headers["WWW-Authenticate"] = f'Bearer error="{code.value}"'
    return response


__all__ = ["JwtAuthMiddleware"]
