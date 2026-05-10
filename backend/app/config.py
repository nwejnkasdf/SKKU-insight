"""SKKU InSight backend settings — pydantic_settings.BaseSettings.

본 모듈은 docs/ops/env-vars.md 의 모든 환경변수를 타입 검증과 함께 캡슐화한다.
부팅 시 누락/타입 오류는 즉시 실패 (Phase 0b A2 가 lifespan validator 추가).
비밀값은 로그 마스킹 (Phase 0b A2 의 로깅 미들웨어 책임).

새 환경변수 추가는 본 파일 + docs/ops/env-vars.md + .env.example 셋 동시 갱신
(에이전트 헌법 §4).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.contracts import AdminRole, LLMProviderType


class Settings(BaseSettings):
    """모든 환경변수의 단일 entry point. Phase 0a stub — Phase 0b 가 validator 추가."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # === Postgres ===
    POSTGRES_DB: str = "insight"
    POSTGRES_USER: str = "insight"
    POSTGRES_PASSWORD: str = ""
    DATABASE_URL: str = (
        "postgresql+asyncpg://insight:changeme@postgres:5432/insight"
    )
    PG_API_POOL_MIN: int = 5
    PG_API_POOL_MAX: int = 30
    PG_WORKER_POOL_MIN: int = 2
    PG_WORKER_POOL_MAX: int = 10

    # === Redis ===
    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_URL_RATE_LIMIT: str = "redis://redis:6379/1"
    REDIS_URL_QUEUE: str = "redis://redis:6379/2"
    REDIS_URL_CACHE: str = "redis://redis:6379/3"

    # === Auth (NFR-15~17) ===
    JWT_SECRET: str = ""  # 빈 값이면 Phase 0b lifespan 이 거부
    JWT_ACCESS_MINUTES: int = 15
    JWT_REFRESH_DAYS: int = 14
    JWT_ISSUER: str = "skku-insight"
    BCRYPT_COST: int = 12

    # === LLM ===
    LLM_PROVIDER: LLMProviderType = LLMProviderType.MOCK
    LLM_MODEL_HIGH: str = "mock-high"
    LLM_MODEL_MEDIUM: str = "mock-medium"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    CODEX_OAUTH_TOKEN: str = ""
    LLM_REQUEST_TIMEOUT_SECONDS: int = 60
    LLM_DAILY_TOKEN_BUDGET: int = 1_000_000
    LLM_MAX_CONCURRENT: int = 8
    LLM_MAX_CONCURRENT_PER_USER: int = 2

    # === Clickbait module (clickbait_module/ 자체 호스팅 또는 외부) ===
    CLICKBAIT_SERVICE_URL: str = ""
    CLICKBAIT_MODEL_NAME: str = "ax-4.0-light-dora-clickbait-v1"

    # === Admin bootstrap ===
    ADMIN_BOOTSTRAP_EMAIL: str = "admin@insight.test"
    # 정책 위반 회피: "admin" 금칙어 + email local "admin" 포함 차단 룰 통과해야 함.
    ADMIN_BOOTSTRAP_PASSWORD: str = "Bootstrap-Initial-2026-Strong!"
    ADMIN_BOOTSTRAP_ROLE: AdminRole = AdminRole.SUPER

    # === Rate limit (slowapi format) ===
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_SIGNUP: str = "3/hour"
    RATE_LIMIT_DEFAULT: str = "60/minute"
    RATE_LIMIT_RUN_NOW: str = "1/hour"
    RATE_LIMIT_REVOKE_CONSENT: str = "5/hour"
    RATE_LIMIT_DELETE_ACCOUNT: str = "1/hour"
    RATE_LIMIT_ONBOARDING: str = "5/hour"
    RATE_LIMIT_ONBOARDING_UPDATE: str = "10/hour"
    RATE_LIMIT_EVENTS: str = "600/minute"

    # === Schedule (cron, UTC) ===
    COLLECTION_CRON: str = "0 3 * * *"
    COLLECTION_CRON_DEMO: str = "0 * * * *"  # demo 모드 — 매시 트리거
    COLLECTION_PER_USER_PARALLEL: int = 4
    COLLECTION_GLOBAL_CONCURRENCY: int = 8
    COLLECTION_USER_JITTER_SECONDS: int = 300
    LIFECYCLE_EVALUATOR: Literal["hybrid_d", "batch_llm", "rule_only"] = "hybrid_d"
    MERGE_EVALUATION_CRON: str = "0 3 * * 1"
    INTEREST_DECAY_CRON: str = "0 0 * * *"
    NAVER_CLEANUP_CRON: str = "0 17 * * *"  # decision-backlog P1-6, NFR-25

    # === Concurrency guards (sdd/concurrency.md) ===
    EVENT_BATCH_SIZE: int = 20
    EVENT_BATCH_FLUSH_SECONDS: int = 5
    RECOMMENDATION_CACHE_TTL_SECONDS: int = 3600
    RECOMMENDATION_BUILD_LOCK_TTL_SECONDS: int = 30
    TRAVERSAL_USER_LOCK_TTL_SECONDS: int = 10
    CONSENT_CACHE_TTL_SECONDS: int = 60

    # === External sources ===
    OPENALEX_POLITE_EMAIL: str = "dev@insight.test"
    SEMANTIC_SCHOLAR_API_KEY: str = ""

    # === CORS / hosts / logging ===
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3001,app://insight"
    API_PUBLIC_BASE: str = "http://localhost:8000"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    STRUCTLOG_RENDER: Literal["json", "console"] = "json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """캐시된 Settings 인스턴스. FastAPI Depends(get_settings) 패턴."""
    return Settings()


__all__ = ["Settings", "get_settings"]
