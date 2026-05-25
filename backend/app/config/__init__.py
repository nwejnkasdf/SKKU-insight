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
    # OpenAI reasoning_effort (gpt-5/o-series). 가능 값: none, minimal, low,
    # medium, high, xhigh (openai-python `types/shared/reasoning_effort.py`).
    # 사용자 결정 (2026-05-18): high slot → "high", medium slot → "medium".
    # xhigh 는 latency 증가 + 5시간 ChatGPT 세션 한도 초과 우려로 미사용. reasoning
    # 모델이 아닌 경우 (gpt-4o 등) 본 값은 openai.py 가 model name prefix 로
    # 분기해서 payload 에서 제외 — chat/completions 400 Unsupported value 차단.
    LLM_REASONING_EFFORT_HIGH: str = "high"
    LLM_REASONING_EFFORT_MEDIUM: str = "medium"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    CODEX_OAUTH_TOKEN: str = ""
    LLM_REQUEST_TIMEOUT_SECONDS: int = 180
    LLM_DAILY_TOKEN_BUDGET: int = 1_000_000_000_000
    # (C-64, 2026-05-26) 시연 환경 burst 확보 — global 8 → 32 (per-user 16 의 ≥2배).
    LLM_MAX_CONCURRENT: int = 32
    # (C-62 후속 round2 → C-64, 2026-05-26) 2 → 4 → 16 단계 상향.
    # COLLECTION_PER_USER_PARALLEL=16 정합 — 한 user collection 의 leaf 병렬 cap 과 일치.
    LLM_MAX_CONCURRENT_PER_USER: int = 16

    # === CodexOAuthProvider (2026-05-18 본문) ===
    # `LLM_PROVIDER=codex_oauth` 토글 시 사용. `~/.codex/auth.json` 의 ChatGPT
    # OAuth 토큰 재사용 — OpenAI 공식 허용 path. backend 컨테이너에 codex CLI
    # 설치 (`npm i -g @openai/codex`) + 호스트 `~/.codex` volume mount 전제.
    CODEX_CLI_PATH: str = "codex"  # PATH 의 codex binary
    # codex sandbox 정책. read-only 권장 — codex 가 backend 컨테이너 파일을 임의
    # mutation 못 하도록. 가능 값: read-only / workspace-write / danger-full-access.
    CODEX_SANDBOX_MODE: str = "read-only"
    # codex 가 사용할 격리 작업 디렉토리 (--cd). git repo 검출 회피 + 외부 파일
    # 차단. /tmp 또는 dedicated tmpfs.
    CODEX_WORKDIR: str = "/tmp/codex-runtime"
    # web_search 모드 — cached (default, 안정) vs live (최신성). search_with_tools
    # 호출 시 cached 면 codex 가 자체 cache 사용, live 면 `--search` flag 로 실
    # 검색. 1차 시연 default = cached (latency / 비용 안정).
    CODEX_WEB_SEARCH_MODE: Literal["cached", "live"] = "cached"
    # codex `service_tier` — fast (default, 우선순위 큐 + 빠른 응답) / default /
    # flex / scale / priority. 사용자 결정 (2026-05-18): codex 활용 시 모든 호출
    # 에 `fast` 적용. 5시간 ChatGPT 세션 한도 안에서 단일 호출 latency 줄이는 게
    # 시연 안정성에 직접 영향. codex가 모델/요금제 호환 자동 처리.
    CODEX_SERVICE_TIER: str = "fast"
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
    ADMIN_BOOTSTRAP_EMAIL: str = "admin@skkuinsight.org"
    # 정책 위반 회피: "admin" 금칙어 + email local "admin" 포함 차단 룰 통과해야 함.
    ADMIN_BOOTSTRAP_PASSWORD: str = "Bootstrap-Initial-2026-Strong!"
    ADMIN_BOOTSTRAP_ROLE: AdminRole = AdminRole.SUPER
    ADMIN_SIGNUP_CODE: str = ""
    ADMIN_SIGNUP_ROLE: AdminRole = AdminRole.SUPER

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
    COLLECTION_PER_USER_PARALLEL: int = 16
    COLLECTION_GLOBAL_CONCURRENCY: int = 8
    COLLECTION_USER_JITTER_SECONDS: int = 300
    LIFECYCLE_EVALUATOR: Literal["hybrid_d", "batch_llm", "rule_only"] = "hybrid_d"
    MERGE_EVALUATION_CRON: str = "0 3 * * 1"
    # A6 daily decay (interest-bayesian.md §2 + decision: lazy 미사용, cron only).
    # 18:00 UTC = 03:00 KST — 사용자 활동 적은 시간대.
    INTEREST_DECAY_CRON: str = "0 18 * * *"
    # A7 신규 (decisions.md §12 결정 #14): collection cron 직후 30분 시점에 emerging
    # 식별 LLM 호출 (사용자별). COLLECTION_CRON=0 3 * * * 면 본 cron = 30 3 * * *.
    LEAF_LIFECYCLE_CRON: str = "30 3 * * *"
    NAVER_CLEANUP_CRON: str = "0 17 * * *"  # (v13 라운드 폐기, 2026-05-11) decision-backlog P1-6 무효 — NaverBS4 어댑터 폐기. env 보존만, scheduler 등록 제거.

    # === Concurrency guards (sdd/concurrency.md) ===
    EVENT_BATCH_SIZE: int = 20
    EVENT_BATCH_FLUSH_SECONDS: int = 5
    RECOMMENDATION_CACHE_TTL_SECONDS: int = 3600
    RECOMMENDATION_BUILD_LOCK_TTL_SECONDS: int = 30
    TRAVERSAL_USER_LOCK_TTL_SECONDS: int = 10
    CONSENT_CACHE_TTL_SECONDS: int = 60

    # === A8 Recommendation Engine (recommendation-ranking.md + cold-start.md) ===
    # decision-backlog C-40 (A8 round 1). 결정 매트릭스는 decisions.md §13.
    #
    # single-flight (sdd/concurrency.md §2). lock token 은 uuid4 + Lua atomic CAS DEL.
    RECOMMENDATION_BUILD_POLL_TIMEOUT_SECONDS: int = 8
    RECOMMENDATION_BUILD_POLL_INTERVAL_SECONDS: float = 0.2
    # cold-start LLM (cold-start.md §비용 가드). 전역 일 cap + 사용자 lifetime cap.
    COLD_START_MAX_PER_DAY: int = 100
    COLD_START_MAX_PER_USER_LIFETIME: int = 3
    # cold-start LLM 호출 timeout (초). 일반 LLM_REQUEST_TIMEOUT_SECONDS=180 과 정합.
    COLD_START_LLM_TIMEOUT_SECONDS: int = 180
    # cold-start orchestrator 가 pseudo Document 매칭 시 사용할 dedup window (일).
    # A4 _DEDUP_WINDOW_DAYS=30 과 동일 (기존 LLM 검색 결과 재활용).
    COLD_START_DEDUP_WINDOW_DAYS: int = 30
    # POST /recommendations/dashboard/refresh slowapi rate limit.
    RATE_LIMIT_DASHBOARD_REFRESH: str = "20/minute"
    # GET /documents/{id}/summary LLM timeout (초). DB 영속 캐시 (DocumentSummaryCache) 가
    # 1차 SOR — 본 timeout 은 miss 시 LLM 호출 한계.
    DOCUMENT_SUMMARY_LLM_TIMEOUT_SECONDS: int = 60
    # Document.summary fallback 시 표시할 최대 문자 수 (LLM 실패 시 source_abstract 1 섹션).
    DOCUMENT_SUMMARY_SOURCE_ABSTRACT_MAX_CHARS: int = 500

    # === A6 Interest Bayesian (algorithms/interest-bayesian.md) ===
    # 1-hop trace path 조상 propagation. A7 본문 PR-3 (2026-05-17) 머지로 default true.
    # false 로 명시 토글 시 ingest_event 는 단일 노드 (부모 cso_topic_id) + 직접 지정 토픽만 갱신.
    INTEREST_PROPAGATION_ENABLED: bool = True
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
    # (C-59, 2026-05-25) Strict 검증 룰 5: leaf 라벨이 anchor_set 의 cso label 과
    # 유사도 ≥ 임계 시 reject "cso_exists". 사용자 의도 정합: leaf = CSO 14k 에 없는
    # 신생 토픽만. 이미 CSO 에 있는 토픽 (예: rag, agentic ai) 은 leaf 만들 필요 X.
    # cluster root + 1-hop 자식 라벨만 비교 (전체 14k 비교는 비용 큼).
    LEAF_EMERGING_CSO_DEDUP_THRESHOLD: float = 0.75
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
    # (C-62, 2026-05-25) 10 → 20 상향 — bootstrap_interest_state 가 사용자 선택 cluster
    # 마다 boost trace INSERT (최대 12) + behavioral trace 여유 8.
    TRACE_ACTIVE_CAP: int = 20
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

    # === A8-v2 UserProfile + Discovery Fusion + Reincarnation (algorithms/recommendation-ranking.md §Discovery) ===
    # decision-backlog C-42 (A8-v2 round 1). 결정 매트릭스는 decisions.md §15.
    #
    # daily LLM cron 시각 — A6 18 UTC + A7 18 UTC 와 분리해서 19 UTC. 사용자 활동
    # 적은 시간대 + A6/A7 의 user-mutex 와 충돌 회피.
    USER_PROFILE_CRON: str = "0 19 * * *"
    # (C-53, 2026-05-24) Fusion bridge BFS / Reincarnation softmax sampling 파라미터.
    # algorithm: backend/app/traversal/fusion_bridge.py + backend/app/profile/sampling.py.
    REINCARNATION_SAMPLING_TEMPERATURE: float = 0.3  # softmax T (0.05~∞), top 70~80% weight
    FUSION_BRIDGE_PATH_TOP_K: int = 5  # 각 path 의 long_score DESC top_k 출발점
    FUSION_BRIDGE_MAX_HOPS: int = 3  # 외향 BFS 최대 깊이 (sparse 그래프 기준 충분)
    # discovery/adjacent → core promotion 주 1회 cron. UserEvent.save 7-day window.
    WEEKLY_PROMOTION_CRON: str = "0 18 * * 0"  # 일요일 18 UTC (= 월요일 03 KST)
    # LLM input archive 필터 — score_tail >= 본 임계 archived trace 만 input 포함.
    # 사용자 결정 #6 (2026-05-19): 강한 신호로 끝난 archive 만 reincarnation candidate
    # 풀에 들어가고, 자연 둔화로 끝난 archive 는 노이즈로 간주.
    USER_PROFILE_ARCHIVE_SCORE_TAIL_MIN: float = 0.6
    # prompt template 버전 추적. UserProfile.generator_version 컬럼 값. prompt 또는
    # output schema 변경 시 bump → daily cron 매일 갱신이라 자연 교체.
    USER_PROFILE_GENERATOR_VERSION: str = "v1"
    # LLM input archive 상한 (token 폭주 가드). 활성 사용자가 archive 누적 시
    # score_tail DESC 정렬 상위 N 만 LLM 에 전달.
    USER_PROFILE_INPUT_ARCHIVE_MAX: int = 8
    # Reincarnation 가드: archived_at 직후 본 active day 미만 archive 는 reincarnation
    # 후보 제외 (너무 최근 archive — 자연 망각 시간 부재).
    USER_PROFILE_REINCARNATION_GAP_DAYS_MIN: int = 7
    # daily cron lock TTL (초). LLM 호출 동반 — Codex R1 Critical #1 fix (2026-05-19):
    # 직전 180s == LLM_REQUEST_TIMEOUT_SECONDS 라 LLM call 도중 lock 만료 race 위험.
    # 2x LLM timeout + commit/cache 오버헤드 마진. A7 traversal_lock 패턴 답습 + 확장.
    USER_PROFILE_LOCK_TTL_SECONDS: int = 360
    # UserProfile fetch 시 Redis cache TTL (초). engine.build_dashboard 가 1회 fetch
    # 후 SETEX. daily cron 완료 후 DEL 로 invalidate.
    USER_PROFILE_CACHE_TTL_SECONDS: int = 3600
    # (C-54, 2026-05-24) Fusion bridge_cso 영역의 fresh Document fetch — UserProfile cron
    # 안에서 BFS bridge 결정 직후 LLM web_search 호출 + Document/DocumentTopic INSERT.
    # 사용자 결정 매트릭스 (decisions.md §17): A1 cron 안 / B2 trace saved + 직전 fetch
    # 회피 / C1 collection schema / D bridge_cso 단일 매핑 / E1 매일 fresh / F1 실패
    # 보존. 사용자당 LLM 1회/일 추가.
    FUSION_FETCH_ENABLED: bool = True
    # bridge fetch 1회 당 LLM 결과 Document 수 cap. collection_job 과 동일.
    FUSION_FETCH_MAX_DOCUMENTS: int = 5
    # P1 — prompt dedup hint 위해 prompt context 에 포함할 "직전 fusion fetch URL/title"
    # 윈도우 (days). Recommendation 의 origin_type='fusion' + 본 window 안 row 조회 결과.
    FUSION_FETCH_RECENT_URLS_WINDOW_DAYS: int = 30

    # === External sources ===
    # (v13 라운드 dead, 2026-05-11) source 어댑터 6종 폐기로 본 두 env 미사용.
    # 향후 어댑터 재도입 가능성 위해 보존만.
    OPENALEX_POLITE_EMAIL: str = "dev@insight.test"
    SEMANTIC_SCHOLAR_API_KEY: str = ""
    # CSO 3.5 다운로드 URL (decision-backlog P1-5). A3 가 `backend/scripts/import_cso.py` 에서 사용.
    # 캐시 파일명 = URL basename (CSO.3.5.csv). git-tracked `data/cso/CSO.3.5.csv` 를
    # `make seed-cso-cache` (FILE 생략) 로 cso_cache volume 에 카피하면 URL 다운로드 skip
    # (오프라인 시연 + KMI 서버 트래픽 절감, C-46 2026-05-24).
    # 향후 버전 갱신 시 본 env 교체 + `data/cso/CSO.X.Y.csv` commit + Makefile default FILE 갱신.
    CSO_DOWNLOAD_URL: str = "https://cso.kmi.open.ac.uk/downloads/CSO.3.5.csv"

    # === CORS / hosts / logging ===
    CORS_ALLOWED_ORIGINS: str = (
        "http://localhost:3001,http://localhost:5173,http://127.0.0.1:5173,app://insight"
    )
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
