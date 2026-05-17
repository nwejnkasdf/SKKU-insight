"""SKKU InSight backend settings — pydantic_settings.BaseSettings.

본 모듈은 docs/ops/env-vars.md 의 모든 환경변수를 타입 검증과 함께 캡슐화한다.
부팅 시 누락/타입 오류는 즉시 실패 (Phase 0b A2 가 lifespan validator 추가).
비밀값은 로그 마스킹 (Phase 0b A2 의 로깅 미들웨어 책임).

새 환경변수 추가는 본 파일 + docs/ops/env-vars.md + .env.example 셋 동시 갱신
(에이전트 헌법 §4).

A3 (CSO Topic Engine, 2026-05-11): config.py 모듈을 패키지로 변환. `broad_interests.toml`
(BroadInterest 12 entry 시드 SOR) 가 본 패키지 안에 함께 거주. import API
(`from app.config import get_settings`) 는 변경 없음.
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
    # (v13 round 2, 2026-05-16) GPT-5.5 default. ModelSlot high/medium 슬롯 양쪽 모두
    # env 로 토글 가능 (운영자가 다른 모델로 분리 운용 시). 사용자 결정 — Claude/Anthropic
    # 시연·운영 어디서도 사용 X. lifespan 가드 (S-08) 가 LLM_PROVIDER ∈ {mock, openai}
    # 외 토글 차단.
    LLM_MODEL_HIGH: str = "gpt-5.5"
    LLM_MODEL_MEDIUM: str = "gpt-5.5"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    CODEX_OAUTH_TOKEN: str = ""
    LLM_REQUEST_TIMEOUT_SECONDS: int = 180
    LLM_DAILY_TOKEN_BUDGET: int = 1_000_000
    LLM_MAX_CONCURRENT: int = 8
    LLM_MAX_CONCURRENT_PER_USER: int = 2
    # decision-backlog C-19: 분산 semaphore acquire 시도 timeout (초). 초과 시
    # LLMBudgetExceeded 와 동일하게 fallback 경로 진입.
    LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS: int = 30

    # === Clickbait module (clickbait_module/ 자체 호스팅 또는 외부) ===
    CLICKBAIT_SERVICE_URL: str = ""
    CLICKBAIT_MODEL_NAME: str = "ax-4.0-light-dora-clickbait-v1"
    # (v13 라운드, 2026-05-11) A4 Topic-driven Pivot — clickbait 통합 default 비활성.
    # 사용자가 News 소스 명시 활성화 시 (true) 만 A4 orchestrator 가 post-filter 로 호출.
    # 1차 시연 default false — LLM 검색이 1차 정제 담당이라 clickbait 불필요.
    CLICKBAIT_ENABLED: bool = False

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
    # A6 daily decay (interest-bayesian.md §2 + decision: lazy 미사용, cron only).
    # 18:00 UTC = 03:00 KST — 사용자 활동 적은 시간대.
    INTEREST_DECAY_CRON: str = "0 18 * * *"
    NAVER_CLEANUP_CRON: str = "0 17 * * *"  # (v13 라운드 폐기, 2026-05-11) decision-backlog P1-6 무효 — NaverBS4 어댑터 폐기. env 보존만, scheduler 등록 제거.

    # === Concurrency guards (sdd/concurrency.md) ===
    EVENT_BATCH_SIZE: int = 20
    EVENT_BATCH_FLUSH_SECONDS: int = 5
    RECOMMENDATION_CACHE_TTL_SECONDS: int = 3600
    RECOMMENDATION_BUILD_LOCK_TTL_SECONDS: int = 30
    TRAVERSAL_USER_LOCK_TTL_SECONDS: int = 10
    CONSENT_CACHE_TTL_SECONDS: int = 60

    # === A6 Interest Bayesian (algorithms/interest-bayesian.md) ===
    # 1-hop trace path 조상 propagation. A7 (leaf-lifecycle + traversal) 도입 후 true.
    # false 일 때 ingest_event 는 단일 노드 (부모 cso_topic_id) + 직접 지정 토픽만 갱신.
    INTEREST_PROPAGATION_ENABLED: bool = False
    # Onboarding 직후 boost (alpha_prior 추가) 가 만료되는 active_day 한도.
    # decay daily cron 이 `user.active_day_counter - boost_applied_at_active_day >= N` row
    # 의 boost 분 (cluster +1.0, 1-hop child +0.5) 을 alpha 에서 차감 + 컬럼 NULL 화.
    INTEREST_BOOST_EXPIRY_ACTIVE_DAYS: int = 14
    # EventBuffer flush 시 per-user mutex 의 TTL (초). traversal_lock 과 분리되어
    # 동시 trace mutation 차단 X. flush 후 즉시 release.
    INTEREST_BATCH_FLUSH_USER_LOCK_TTL_SECONDS: int = 10
    # dwell_tick 카운터 Redis TTL (초). 문서당 cap 4회 (≥2분) 도달 후 1시간 동안
    # 추가 dwell 이 와도 베이지안 갱신 skip — 1시간 지나면 자연 재시작.
    DWELL_TICK_CAP_TTL_SECONDS: int = 3600
    DWELL_TICK_CAP_PER_DOCUMENT: int = 4
    # event idempotency payload-hash 캐시 TTL (초). client retry window 가정.
    # DB UNIQUE(user_id, client_request_id) 가 1차 SOR.
    EVENT_DUPLICATE_CACHE_TTL_SECONDS: int = 86400
    # Codex S-05 fix: system_config seed (interest_params, event_weights) 누락 시 lifespan
    # 동작 모드. true (default 운영) → SystemConfigMissingError 로 startup 차단 (fail-fast).
    # false → WARN + endpoint fallback (테스트 환경 / 의도적 비활성).
    SYSTEM_CONFIG_REQUIRED: bool = True

    # === A7 Leaf Lifecycle + Traversal (algorithms/leaf-topic-lifecycle.md + cso-topic-traversal.md) ===
    # decision-backlog C-39 (A7 round 1). 결정 매트릭스 23건은 decisions.md §12.
    #
    # --- leaf 식별 (emerging) ---
    # max_new_leaves_per_day. LLM 응답에서 confidence 내림차순 상위 N 만 채택.
    LEAF_EMERGING_MAX_PER_DAY: int = 3
    # Strict 검증: confidence ≥ 0.6 미만 candidate 자동 거부.
    LEAF_EMERGING_CONFIDENCE_MIN: float = 0.6
    # Strict 검증: supporting_documents ≥ 3 미만 candidate 자동 거부.
    LEAF_EMERGING_SUPPORTING_DOCUMENTS_MIN: int = 3
    # Strict 검증: 기존 active leaf 라벨 의미유사도 ≥ 임계 시 dedup (신규 거부).
    # 1차 시연은 Levenshtein 정규화 사용 (임베딩 미사용, decisions.md §3).
    LEAF_EMERGING_LABEL_SIMILARITY_DEDUP: float = 0.75
    # LLM input 시간 window. A4 collection 결과 (DocumentTopic.leaf_topic_id IN
    # user_leaves OR ...) union UserEvent click/save Document. 결정 매트릭스 #18 옵션 D.
    LEAF_EMERGING_INPUT_WINDOW_HOURS: int = 24
    # trace_anchor_required 위반 시 LLM 재호출 cap. retry 후에도 모두 위반이면
    # 빈 응답 fallback + warning log.
    LEAF_LLM_ANCHOR_RETRY_CAP: int = 1
    # daily emerging 식별 cron lock TTL.
    LEAF_LIFECYCLE_LOCK_TTL_SECONDS: int = 60

    # --- leaf 룰 기반 전이 ---
    # emerging → active 승격 (window 내 문서/관심신호 임계).
    LEAF_ACTIVE_WINDOW_DAYS: int = 7
    LEAF_ACTIVE_MIN_DOCUMENTS: int = 5
    LEAF_ACTIVE_MIN_INTEREST_SIGNALS: int = 2
    # active → stale 강등 (idle active days).
    LEAF_STALE_IDLE_DAYS: int = 21
    # stale → archived 폐기 (idle active days).
    LEAF_ARCHIVED_IDLE_DAYS: int = 90
    # emerging → archived 폐기 (승격 전 idle 만료).
    LEAF_EMERGING_ARCHIVED_IDLE_DAYS: int = 14
    # stale → active 재활성화 (window 내 문서/관심신호 임계).
    LEAF_REACTIVATION_WINDOW_DAYS: int = 7
    LEAF_REACTIVATION_MIN_DOCUMENTS: int = 3
    LEAF_REACTIVATION_MIN_INTEREST_SIGNALS: int = 1

    # --- leaf 병합 (LLM 주 1회) ---
    LEAF_MERGE_JACCARD_MIN: float = 0.6
    LEAF_MERGE_LABEL_SIMILARITY_MIN: float = 0.75
    LEAF_MERGE_MAX_PER_USER: int = 50
    MERGE_EVALUATION_LOCK_TTL_SECONDS: int = 120

    # --- trace operation (extend/retract/split/archive/merge) ---
    # cso-topic-traversal.md §11 cap. archive auto 임계.
    TRACE_ACTIVE_CAP: int = 10
    TRACE_PATH_DEPTH_CAP: int = 8
    # 1단계 stale 마킹 (ingest 직후 즉시, no LLM). path 말단 score_tail ≤ 임계
    # AND idle ≥ N active days.
    TRACE_STALE_IDLE_DAYS: int = 21
    TRACE_STALE_THRESHOLD_SCORE: float = 0.30
    # 2단계 retract (stale 누적 추가 14 days → daily cron 시 LLM 재배치 + path.pop).
    TRACE_RETRACT_AFTER_STALE_DAYS: int = 14
    # 3단계 archive (stale 누적 90 days → status='archived', no LLM).
    TRACE_ARCHIVE_AFTER_STALE_DAYS: int = 90
    # extend operation 트리거 — 자식 노드 인터랙션 ≥ 5건.
    TRACE_EXTEND_MIN_INTERACTIONS: int = 5
    # split operation window — 두 자식 동시 extend 임계 도달 (active days).
    TRACE_SPLIT_WINDOW_DAYS: int = 7
    # merge operation 룰 trigger — path overlap ≥ N cso_topic_id (A7 신규).
    TRACE_MERGE_PATH_OVERLAP_MIN: int = 3
    # daily trace merge cron — 18 UTC (decay 와 같은 시각, 03 KST).
    TRACE_MERGE_CRON: str = "0 18 * * *"
    TRACE_MERGE_LOCK_TTL_SECONDS: int = 120

    # --- propagation (cso-topic-traversal.md §4) ---
    # A6 propagation.py 가 사용. A7 본문 PR-3 와 함께 INTEREST_PROPAGATION_ENABLED 토글.
    PROPAGATION_HOP_DECAY: float = 0.5
    PROPAGATION_MAX_HOPS: int = 4

    # === External sources ===
    # (v13 라운드 dead, 2026-05-11) source 어댑터 6종 폐기로 본 두 env 미사용.
    # 향후 어댑터 재도입 가능성 위해 보존만.
    OPENALEX_POLITE_EMAIL: str = "dev@insight.test"
    SEMANTIC_SCHOLAR_API_KEY: str = ""
    # CSO 3.4 다운로드 URL (decision-backlog P1-5). A3 가 `backend/scripts/import_cso.py` 에서 사용.
    # 신버전(3.5+) 출시 시 본 env 만 교체 후 `make import-cso ARGS="--reset --refresh"`.
    CSO_DOWNLOAD_URL: str = "https://cso.kmi.open.ac.uk/downloads/CSO.3.4.csv"

    # === CORS / hosts / logging ===
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3001,app://insight"
    API_PUBLIC_BASE: str = "http://localhost:8000"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    STRUCTLOG_RENDER: Literal["json", "console"] = "json"

    # === Process / worker (decision-backlog C-20) ===
    # uvicorn worker process 수. 1=single-process (asyncio 만), N>1=multi-process.
    # multi-process 시 LLM semaphore 와 동시성 가드는 Redis-distributed 로 동작
    # (`app/llm_provider/_concurrency.py`). DB pool 은 process 별 독립이므로
    # 총 connection = UVICORN_WORKERS * PG_API_POOL_MAX + (worker container) *
    # PG_WORKER_POOL_MAX 가 PostgreSQL `max_connections` 를 넘지 않도록 운영자가 조정.
    UVICORN_WORKERS: int = 1


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """캐시된 Settings 인스턴스. FastAPI Depends(get_settings) 패턴."""
    return Settings()


__all__ = ["Settings", "get_settings"]
