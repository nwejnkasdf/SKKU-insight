"""FastAPI lifespan — startup 검증 + 리소스 init / shutdown 정리.

startup:
1) JWT_SECRET length≥32 검증 (빈 값 또는 default 차단)
2) POSTGRES_PASSWORD non-empty 검증
3) async DB engine init (api pool)
4) 4 Redis client ping (default/rate_limit/queue/cache)
5) structlog 바인딩 (LOG_LEVEL + STRUCTLOG_RENDER)

shutdown:
1) engine.dispose()
2) redis.aclose() x4
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.config import get_settings
from app.db.engine import dispose_engines, get_engine
from app.middleware.structlog_mask import mask_secrets
from app.redis import RedisDB, close_redis, get_redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan context manager."""
    settings = get_settings()
    _validate_secrets(settings.JWT_SECRET, settings.POSTGRES_PASSWORD)
    _configure_structlog(settings.LOG_LEVEL, settings.STRUCTLOG_RENDER)
    # DB engine 초기화 (api 모드)
    get_engine("api")
    # Redis ping 4개
    for db_name in ("default", "rate_limit", "queue", "cache"):
        client = get_redis(db_name)  # type: ignore[arg-type]
        try:
            pong = await client.ping()
            if not pong:
                raise RuntimeError(f"Redis ping failed for db={db_name}")
        except Exception as exc:
            raise RuntimeError(
                f"Redis 연결 실패 db={db_name}: {exc}"
            ) from exc
    logger = structlog.get_logger("lifespan")
    logger.info(
        "lifespan startup ok",
        provider=settings.LLM_PROVIDER.value,
        log_level=settings.LOG_LEVEL,
    )
    try:
        yield
    finally:
        await dispose_engines()
        await close_redis()
        logger.info("lifespan shutdown ok")


# codex v2 #1: .env.example placeholder 가 길이만 통과해 운영자가 미교체 시
# 공개 HS256 키로 서명 — 토큰 위조 가능. 명시 차단 리스트 + prefix 패턴 차단.
_JWT_SECRET_PLACEHOLDERS = frozenset(
    {
        "change-this-to-64-char-random-secret-please-do-not-leave-default-here",
        "change-me",
        "changeme",
        "your-secret-here",
        "placeholder",
        "secret",
    }
)
_JWT_SECRET_BAD_PREFIXES = ("change-this-to-", "your-secret-", "example-")
_POSTGRES_PASSWORD_PLACEHOLDERS = frozenset(
    {
        "changeme-strong-password",
        "changeme",
        "change-me",
        "password",
        "postgres",
        "placeholder",
    }
)


def _validate_secrets(jwt_secret: str, postgres_password: str) -> None:
    if len(jwt_secret) < 32:
        raise RuntimeError(
            "JWT_SECRET 은 32자 이상이어야 합니다 (env-vars.md 권장 64+). "
            ".env 의 JWT_SECRET 을 확인하세요."
        )
    jwt_lower = jwt_secret.lower()
    if jwt_lower in _JWT_SECRET_PLACEHOLDERS:
        raise RuntimeError(
            "JWT_SECRET 이 .env.example placeholder 값입니다 — 실제 운영 secret 으로 "
            "교체하세요 (64+ 자 random)."
        )
    for prefix in _JWT_SECRET_BAD_PREFIXES:
        if jwt_lower.startswith(prefix):
            raise RuntimeError(
                f"JWT_SECRET 이 template placeholder 패턴입니다 — '{prefix}...' 로 "
                "시작하는 값은 모두 차단됨. 실제 운영 secret 으로 교체하세요."
            )
    if not postgres_password.strip():
        raise RuntimeError(
            "POSTGRES_PASSWORD 가 비어 있습니다. .env 를 확인하세요."
        )
    if postgres_password.lower() in _POSTGRES_PASSWORD_PLACEHOLDERS:
        raise RuntimeError(
            "POSTGRES_PASSWORD 가 placeholder 값입니다 — 실제 운영 secret 으로 교체."
        )


def _configure_structlog(log_level: str, render: str) -> None:
    level = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(level=level)
    renderer = (
        structlog.processors.JSONRenderer()
        if render == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            mask_secrets,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


__all__ = ["lifespan"]
