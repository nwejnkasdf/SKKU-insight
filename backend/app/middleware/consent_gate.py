"""Consent gate 미들웨어 — personalization endpoint 보호 (FR-59).

화이트리스트 (consent 검사 우회):
- /health, /docs, /openapi.json, /redoc
- /auth/*
- /consent/*
- /admin/*
- /onboarding/cold-start-status/* (GET — 폴링은 동의 후 시작했으므로 OK)

그 외 personalization endpoint(/onboarding/interests POST/PUT, /recommendations/*,
/documents/*, /events, /feedback/*, /topics/*, /interest/*) 는 consent active 검증.

미들웨어 단에서는 가벼운 게이트 — 본격 검증은 endpoint 의 Depends(require_consent_active).
본 미들웨어는 user 인증된 요청만 처리하므로 user_id 가 state 에 있다.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from app.contracts import ErrorCode, ErrorResponse, RedisKey, TokenAudience
from app.db.session import AsyncSessionLocal
from app.redis import get_redis
from app.security.consent_cache import is_consent_active

# personalization 보호 경로
PROTECTED_PATTERNS = [
    re.compile(r"^/onboarding/interests/?$"),
    re.compile(r"^/recommendations(/.*)?$"),
    re.compile(r"^/documents(/.*)?$"),
    re.compile(r"^/events(/.*)?$"),
    re.compile(r"^/feedback(/.*)?$"),
    re.compile(r"^/topics(/.*)?$"),
    re.compile(r"^/interest(/.*)?$"),
]


class ConsentGateMiddleware(BaseHTTPMiddleware):
    """consent active 가 아닌 사용자의 personalization endpoint 호출 차단."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        aud = getattr(request.state, "aud", None)
        user_id_str = getattr(request.state, "user_id", None)
        # admin / 미인증 요청은 우회 (admin 은 자체 권한, 미인증은 JWT 미들웨어에서 차단됨)
        if aud != TokenAudience.USER.value or not user_id_str:
            return await call_next(request)
        if not _is_protected(path):
            return await call_next(request)

        # 가벼운 검사: Redis 캐시 hit 이면 즉시 결론. miss 면 DB 조회.
        redis = get_redis("default")
        try:
            user_id = UUID(user_id_str)
        except ValueError:
            return _forbidden(request)

        # 캐시 우선 — DB 세션은 캐시 miss 시에만
        cached = await redis.get(RedisKey.consent_active_cache(user_id))
        if cached is not None:
            if cached != "1":
                return _forbidden(request)
            return await call_next(request)
        async with AsyncSessionLocal() as session:  # type: AsyncSession
            active = await is_consent_active(user_id, redis, session)
        if not active:
            return _forbidden(request)
        return await call_next(request)


def _is_protected(path: str) -> bool:
    return any(p.match(path) for p in PROTECTED_PATTERNS)


def _forbidden(request: Request) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        code=ErrorCode.CONSENT_REQUIRED,
        message="개인화 동의가 필요합니다.",
        details={"reauth_required": True},
        request_id=request_id,
    ).model_dump(mode="json")
    return JSONResponse(status_code=403, content=body)


__all__ = ["ConsentGateMiddleware"]
