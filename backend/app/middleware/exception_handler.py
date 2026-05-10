"""예외 → ErrorResponse 변환 핸들러.

FastAPI app.add_exception_handler 로 등록:
- RequestValidationError → 422 VALIDATION_ERROR (Pydantic validator 실패)
- HTTPException → 그대로 status + ErrorResponse (이미 detail 이 dict 형식이면 그대로)
- 기타 Exception → 500 INTERNAL_ERROR (보안 — 내부 메시지 노출 안 함)
"""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.contracts import ErrorCode, ErrorResponse

logger = logging.getLogger(__name__)


async def validation_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Pydantic validation 실패 → 422."""
    if not isinstance(exc, RequestValidationError):
        raise exc
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        code=ErrorCode.VALIDATION_ERROR,
        message="요청 본문이 유효하지 않습니다.",
        details={"errors": exc.errors()},
        request_id=request_id,
    ).model_dump(mode="json")
    return JSONResponse(status_code=422, content=body)


async def http_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """HTTPException → status + ErrorResponse.

    service 가 raise 한 HTTPException 의 detail 이 ErrorResponse 형식 dict 이면 그대로 반환.
    아니면 fallback ErrorResponse.
    """
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    request_id = getattr(request.state, "request_id", None)
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        # service 가 ErrorResponse 형식으로 생성한 경우 그대로
        body = dict(exc.detail)
        if not body.get("request_id"):
            body["request_id"] = request_id
        return JSONResponse(status_code=exc.status_code, content=body)
    # fallback
    body = ErrorResponse(
        code=_map_status_to_code(exc.status_code),
        message=str(exc.detail) if exc.detail else "오류가 발생했습니다.",
        request_id=request_id,
    ).model_dump(mode="json")
    return JSONResponse(status_code=exc.status_code, content=body)


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """예상치 못한 예외 → 500. 내부 메시지 노출 X (보안)."""
    logger.exception("unhandled exception path=%s", request.url.path)
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        code=ErrorCode.INTERNAL_ERROR,
        message="서버 내부 오류가 발생했습니다.",
        request_id=request_id,
    ).model_dump(mode="json")
    return JSONResponse(status_code=500, content=body)


def _map_status_to_code(status_code: int) -> ErrorCode:
    if status_code == 401:
        return ErrorCode.AUTH_INVALID_TOKEN
    if status_code == 403:
        return ErrorCode.CONSENT_REQUIRED
    if status_code == 422:
        return ErrorCode.VALIDATION_ERROR
    if status_code == 429:
        return ErrorCode.RATE_LIMITED
    return ErrorCode.INTERNAL_ERROR


__all__ = [
    "http_exception_handler",
    "unhandled_exception_handler",
    "validation_exception_handler",
]
