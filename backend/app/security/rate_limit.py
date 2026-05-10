"""slowapi rate limit — Limiter + ErrorResponse 변환 핸들러.

key_func 는 인증된 사용자면 user_id, 아니면 IP. fail_open=True 로 Redis 다운 시 차단 안 함
(시연 안정성 > 가용성, 사용자 결정).

429 응답 → ErrorResponse(code=AUTH_RATE_LIMITED 또는 endpoint 별) + Retry-After 헤더.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings
from app.contracts import ErrorCode, ErrorResponse


def _key_func(request: Request) -> str:
    """인증된 요청이면 user_id, 그 외 IP."""
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"


_settings = get_settings()

limiter = Limiter(
    key_func=_key_func,
    storage_uri=_settings.REDIS_URL_RATE_LIMIT,
    strategy="fixed-window",
    swallow_errors=True,  # Redis 다운 시 통과 (fail_open)
    default_limits=[_settings.RATE_LIMIT_DEFAULT],
)


async def rate_limit_exceeded_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """slowapi 의 429 응답을 ErrorResponse 형식으로 변환."""
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    retry_after = int(exc.detail.split()[0]) if exc.detail else 60
    request_id = getattr(request.state, "request_id", None)
    response = JSONResponse(
        status_code=429,
        content=ErrorResponse(
            code=ErrorCode.AUTH_RATE_LIMITED,
            message="요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
            details={"retry_after_seconds": retry_after},
            request_id=request_id,
        ).model_dump(mode="json"),
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


__all__: list[Any] = ["limiter", "rate_limit_exceeded_handler"]
