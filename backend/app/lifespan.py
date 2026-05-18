"""FastAPI lifespan — startup 검증 + 리소스 init / shutdown 정리.

startup:
1) JWT_SECRET length≥32 검증 (빈 값 또는 default 차단)
2) POSTGRES_PASSWORD non-empty 검증
3) async DB engine init (api pool)
4) 4 Redis client ping (default/rate_limit/queue/cache)
5) structlog 바인딩 (LOG_LEVEL + STRUCTLOG_RENDER)
6) A6 system_config 로더 — interest_params + event_weights 캐시 SETEX
7) A6 EventBuffer 인스턴스 + flush_periodic background task 등록

shutdown:
1) EventBuffer.stop() final flush
2) engine.dispose()
3) redis.aclose() x4
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.config import get_settings
from app.contracts import LLMProviderType
from app.db.engine import dispose_engines, get_engine
from app.db.session import AsyncSessionLocal
from app.events.buffer import EventBuffer
from app.interest.config_loader import (
    SystemConfigMissingError,
    load_system_config,
)
from app.middleware.structlog_mask import mask_secrets
from app.redis import close_redis, get_redis
from app.topic.lifespan import topic_shutdown, topic_startup


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan context manager."""
    settings = get_settings()
    _validate_secrets(settings.JWT_SECRET, settings.POSTGRES_PASSWORD)
    _validate_llm_provider(settings.LLM_PROVIDER)
    _configure_structlog(settings.LOG_LEVEL, settings.STRUCTLOG_RENDER)
    # DB engine 초기화 (api 모드)
    get_engine("api")
    # Redis ping 4개
    for db_name in ("default", "rate_limit", "queue", "cache"):
        client = get_redis(db_name)
        try:
            pong: bool = await client.ping()  # type: ignore[misc]
            if not pong:
                raise RuntimeError(f"Redis ping failed for db={db_name}")
        except Exception as exc:
            raise RuntimeError(
                f"Redis 연결 실패 db={db_name}: {exc}"
            ) from exc
    logger = structlog.get_logger("lifespan")
    # A3: NetworkX CSO 그래프 빌드 + app.state.cso_graph 등록 (결정 6).
    # cso_topic 비어 있으면 빈 그래프 등록 + WARN (test 환경 호환). verify 는 skip.
    await topic_startup(app)

    # A6: system_config 로더 — interest_params + event_weights 캐시 SETEX.
    # Codex S-05 fix: SYSTEM_CONFIG_REQUIRED=true (default 운영) → fail-fast (RuntimeError).
    # false (테스트 / 의도적 비활성) → WARN 후 startup 계속, endpoint 가 fallback DB 로드.
    redis_default = get_redis("default")
    async with AsyncSessionLocal() as session:
        try:
            await load_system_config(session, redis_default)
            app.state.system_config_loaded = True
        except SystemConfigMissingError as exc:
            app.state.system_config_loaded = False
            if settings.SYSTEM_CONFIG_REQUIRED:
                raise RuntimeError(
                    f"system_config seed 누락 (key={exc.key}) — alembic 0004 또는 "
                    "A10 admin-console 에서 복원. SYSTEM_CONFIG_REQUIRED=false 로 "
                    "운영 가능하나 endpoint 호출 시 503 응답."
                ) from exc
            logger.warning(
                "lifespan: system_config seed 누락 — SYSTEM_CONFIG_REQUIRED=false "
                "이므로 startup 계속. endpoint 호출 시 fallback DB 로드",
                error=str(exc),
                key=exc.key,
            )

    # A6: EventBuffer 인스턴스 + flush_periodic background task 등록.
    # 1차 시연은 router 가 즉시 ingest — buffer 는 활성화 toggle 대비 등록만.
    from app.interest.service import flush_buffered_events

    async def _flush_cb(user_id, entries):  # type: ignore[no-untyped-def]
        cso_graph = getattr(app.state, "cso_graph", None)
        await flush_buffered_events(
            user_id,
            list(entries),
            session_factory=AsyncSessionLocal,
            cso_graph=cso_graph,
            redis=redis_default,
        )

    event_buffer = EventBuffer(
        flush_callback=_flush_cb,
        batch_size=settings.EVENT_BATCH_SIZE,
        flush_seconds=float(settings.EVENT_BATCH_FLUSH_SECONDS),
    )
    app.state.event_buffer = event_buffer
    app.state.event_buffer_task = asyncio.create_task(
        event_buffer.flush_periodic()
    )

    logger.info(
        "lifespan startup ok",
        provider=settings.LLM_PROVIDER.value,
        log_level=settings.LOG_LEVEL,
        system_config_loaded=app.state.system_config_loaded,
    )
    try:
        yield
    finally:
        # A6: EventBuffer 종료 — task cancel + final flush.
        task = getattr(app.state, "event_buffer_task", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        buffer = getattr(app.state, "event_buffer", None)
        if buffer is not None:
            await buffer.stop()
        await topic_shutdown(app)
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


# (Codex round 2 S-08) A4 collection 이 search_with_tools 본문을 구현한 provider 만 허용.
# anthropic / openrouter 는 NotImplementedError 라 runtime crash 방지 목적.
# 2026-05-18: codex_oauth 본문 구현 (`codex exec --json` subprocess wrap) — 화이트리스트
# 추가. anthropic / openrouter 는 여전히 stub.
_SUPPORTED_A4_PROVIDERS = frozenset(
    {
        LLMProviderType.MOCK,
        LLMProviderType.OPENAI,
        LLMProviderType.CODEX_OAUTH,
    }
)


def _validate_llm_provider(provider: LLMProviderType) -> None:
    if provider not in _SUPPORTED_A4_PROVIDERS:
        supported = sorted(p.value for p in _SUPPORTED_A4_PROVIDERS)
        raise RuntimeError(
            f"LLM_PROVIDER={provider.value} 는 A4 collection 미지원 "
            f"(search_with_tools NotImplementedError). 지원 provider: {supported}. "
            ".env 의 LLM_PROVIDER 를 mock / openai / codex_oauth 중 하나로 변경하세요."
        )
    if provider == LLMProviderType.CODEX_OAUTH:
        _validate_codex_cli()


def _validate_codex_cli() -> None:
    """codex_oauth 토글 시 codex CLI binary 사전 검증 (시연 30초 전 부트 가드).

    `codex --version` 1회 실행 — binary 존재 + 실행 가능 여부만 확인.
    OAuth refresh token 만료까지 부트 차단하면 시연 30분 전에 재로그인 강제하는데
    refresh 가 자주 깨지므로 binary 존재까지만 strict. OAuth refresh 실패는
    endpoint 호출 시 ProviderError 로 자연 raise → fallback (또는 시연 운영자
    재로그인) 으로 처리.
    """
    import subprocess

    settings = get_settings()
    try:
        result = subprocess.run(
            [settings.CODEX_CLI_PATH, "--version"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"LLM_PROVIDER=codex_oauth 인데 codex CLI binary 없음 "
            f"(path={settings.CODEX_CLI_PATH!r}). `npm i -g @openai/codex` 후 "
            f"backend 컨테이너 재빌드, 또는 LLM_PROVIDER 를 mock/openai 로 토글."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"codex --version 이 10s 안에 응답 안 함 (path={settings.CODEX_CLI_PATH!r})"
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise RuntimeError(
            f"codex --version exit={result.returncode} stderr={stderr!r}"
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
