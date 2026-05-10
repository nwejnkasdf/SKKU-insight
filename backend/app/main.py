"""FastAPI application entry point — Phase 0a stub.

본 모듈은 모든 router 를 등록해 OpenAPI export 가 동작하도록만 한다.
Phase 0b A2 가 다음을 추가 구현:
- Settings.JWT_SECRET / POSTGRES_PASSWORD 빈 값 거부 lifespan validator
- structlog 바인딩 (LOG_LEVEL, STRUCTLOG_RENDER)
- ErrorResponse 변환 exception handler
- slowapi rate limit middleware
- consent active middleware
- DB pool / Redis 연결 lifespan
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.collection.router import router as collection_router
from app.consent.router import router as consent_router
from app.interest.router import router as interest_router
from app.onboarding.router import router as onboarding_router
from app.recommendation.router import document_router, recommendation_router
from app.topic.router import router as topic_router


def create_app() -> FastAPI:
    """FastAPI 앱 팩토리. test/codegen 시 재사용 가능."""
    app = FastAPI(
        title="SKKU InSight Backend",
        version="0.1.0",
        description=(
            "SKKU InSight 백엔드 (Phase 0a contract-first 게이트 산출). "
            "endpoint body 는 모두 NotImplementedError — Phase 0b A2 부터 구현."
        ),
    )

    # --- CORS skeleton (Phase 0b 가 settings.CORS_ALLOWED_ORIGINS 로 교체) ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3001", "app://insight"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Idempotency-Key",
            "X-Request-Id",
            "Accept-Language",
            "Prefer",
        ],
        expose_headers=[
            "X-Request-Id",
            "X-Server-Time",
            "Retry-After",
            "WWW-Authenticate",
        ],
    )

    # --- X-Request-Id skeleton (Phase 0b 가 echo + structlog binding 추가) ---
    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        return await call_next(request)

    # --- Routers ---
    app.include_router(auth_router)
    app.include_router(consent_router)
    app.include_router(onboarding_router)
    app.include_router(topic_router)
    app.include_router(interest_router)
    app.include_router(collection_router)
    app.include_router(recommendation_router)
    app.include_router(document_router)
    app.include_router(admin_router)

    return app


app = create_app()


__all__ = ["app", "create_app"]
