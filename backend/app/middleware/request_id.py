"""X-Request-Id 미들웨어 — 헤더 echo + structlog binding.

클라이언트가 X-Request-Id 보내면 그대로 echo. 없으면 uuid4 생성.
응답에 동일 헤더 셋팅. structlog 의 contextvars 에 바인딩해 모든 로그에 자동 포함.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class RequestIdMiddleware(BaseHTTPMiddleware):
    """request_id 셋팅 + 응답 헤더 echo + structlog 바인딩."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get("X-Request-Id")
        request_id = (
            incoming if incoming and 8 <= len(incoming) <= 128 else str(uuid4())
        )
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-Id"] = request_id
        return response


__all__ = ["RequestIdMiddleware"]
