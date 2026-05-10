"""FastAPI application entry point — Phase 0b A2 본문.

A2-stub 가 만든 골격 위에 본문 채움:
- lifespan (JWT_SECRET/POSTGRES_PASSWORD 검증, engine/redis init, structlog)
- 미들웨어 4종 (RequestId / JwtAuth / ConsentGate / CORS)
- slowapi rate limit (Limiter + 429 handler)
- exception handler 3종 (Validation / HTTP / unhandled)
- /health endpoint
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.collection.router import router as collection_router
from app.config import get_settings
from app.consent.router import router as consent_router
from app.interest.router import router as interest_router
from app.lifespan import lifespan
from app.middleware.consent_gate import ConsentGateMiddleware
from app.middleware.exception_handler import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.middleware.jwt_auth import JwtAuthMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.onboarding.router import router as onboarding_router
from app.recommendation.router import document_router, recommendation_router
from app.security.rate_limit import limiter, rate_limit_exceeded_handler
from app.topic.router import router as topic_router


def create_app() -> FastAPI:
    """FastAPI 앱 팩토리."""
    settings = get_settings()
    app = FastAPI(
        title="SKKU InSight Backend",
        version="0.1.0",
        description=(
            "SKKU InSight 백엔드 (Phase 0b A2 backend-foundation). "
            "auth / consent / onboarding / admin auth / admin users 본문 구현. "
            "나머지 endpoint 는 후속 에이전트 stub 유지."
        ),
        lifespan=lifespan,
    )

    # slowapi rate limit — 앱에 limiter 바인딩
    app.state.limiter = limiter

    # === 미들웨어 ===
    # Starlette 의 add_middleware 동작 (codex v3 #1 → C-30):
    # - 마지막 add 가 **가장 바깥 (request 들어올 때 먼저 실행)**
    # - 따라서 요청 흐름은 add 호출의 *역순* 으로 진행
    #
    # 의도하는 요청 흐름 (바깥→안쪽):
    #   CORS → RequestId → JwtAuth → ConsentGate → endpoint
    #
    # 사유:
    #   1) CORS 가장 바깥 — JwtAuth/ConsentGate 가 401/403 반환해도 CORS 헤더 적용
    #      (브라우저·Electron 이 cross-origin 으로 ErrorResponse 읽기 가능)
    #   2) RequestId — JwtAuth 이전에 셋팅돼야 모든 인증 실패 로그에 request_id 바인딩
    #   3) JwtAuth — request.state.user_id/aud/jti 셋팅
    #   4) ConsentGate — JwtAuth 가 셋팅한 user_id/aud 를 사용. JwtAuth 이전에
    #      실행되면 user_id 미셋팅 → "user audience 만 검사" 가드가 false →
    #      **모든 protected endpoint 가 consent 검사 없이 통과** (codex v2 fix 의
    #      잘못된 순서가 만든 결함, codex v3 #1 으로 발견)
    #
    # add 호출 순서 (마지막이 가장 바깥):
    #   ConsentGate(첫 add — 가장 안쪽) → JwtAuth → RequestId → CORS(마지막)
    cors_origins = [
        o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()
    ]
    # ConsentGate (가장 안쪽 — endpoint 직전. JwtAuth 가 셋팅한 user_id/aud 사용)
    app.add_middleware(ConsentGateMiddleware)
    # JwtAuth (Bearer + aud + denylist + deletion gate. request.state.user_id 셋팅)
    app.add_middleware(JwtAuthMiddleware)
    # RequestId (JwtAuth 이전에 실행돼 모든 로그에 request_id 바인딩)
    app.add_middleware(RequestIdMiddleware)
    # CORS (가장 바깥 — 401/403 응답에도 CORS 헤더 적용)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
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

    # === Exception handlers ===
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # === Routers ===
    app.include_router(auth_router)
    app.include_router(consent_router)
    app.include_router(onboarding_router)
    app.include_router(topic_router)
    app.include_router(interest_router)
    app.include_router(collection_router)
    app.include_router(recommendation_router)
    app.include_router(document_router)
    app.include_router(admin_router)

    # === Health ===
    @app.get("/health", tags=["health"], summary="헬스 체크")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


__all__ = ["app", "create_app"]
