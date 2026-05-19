"""SKKU InSight contracts — Single Source of Record (SOR).

본 모듈은 모든 enum, error code, Redis key 컨벤션, Pydantic base 모델의 단일 정의처다.
다른 모든 모듈은 본 파일을 import 만 하며, 새 항목은 docs/sdd/contracts.md §8 절차에 따라
사용자 결정 후 별도 PR로만 추가한다.

연관 docs:
- docs/sdd/contracts.md         — 본 파일 명세 (SOR)
- docs/sdd/api-conventions.md   — HTTP 표준 (PageMeta, ErrorResponse 등)
- docs/sdd/agent-orchestration.md — 멀티 에이전트 운영 룰 (5겹 방어)
- docs/sdd/concurrency.md       — Redis key 디자인 근거
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel

# ============================================================
# 1. Enum (13개) — docs/sdd/contracts.md §2
# ============================================================


class EventType(str, Enum):
    """사용자 행동 이벤트 종류. interest.md 참조."""

    VIEW = "view"
    CLICK = "click"
    DWELL_TICK = "dwell_tick"
    OPEN_EXTERNAL = "open_external"
    SAVE = "save"
    HIDE = "hide"
    NOT_INTERESTED = "not_interested"


class ContentType(str, Enum):
    """Document.content_type 컬럼 값."""

    ACADEMIC_PAPER = "academic_paper"
    VENDOR_BLOG = "vendor_blog"
    TECH_NEWS = "tech_news"
    PSEUDO_COLD_START = "pseudo_cold_start"


class SourceType(str, Enum):
    """Source.source_type 컬럼 값. 일반 사용자 응답에도 노출."""

    ACADEMIC = "academic"
    VENDOR_BLOG = "vendor_blog"
    TECH_NEWS = "tech_news"


class TrustLevel(str, Enum):
    """Source 신뢰도. fallback 룰 (FR-42·43) 에서 사용."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SlotType(str, Enum):
    """추천 슬롯. core 5 / adjacent 3 / discovery 2 + fallback 2."""

    CORE = "core"
    ADJACENT = "adjacent"
    DISCOVERY = "discovery"
    FALLBACK_ADJACENT = "fallback_adjacent"
    FALLBACK_TREND = "fallback_trend"


class LeafTopicStatus(str, Enum):
    """동적 리프 토픽 상태. algorithms/leaf-topic-lifecycle.md 참조."""

    EMERGING = "emerging"
    ACTIVE = "active"
    STALE = "stale"
    MERGED = "merged"
    ARCHIVED = "archived"


class TraversalStatus(str, Enum):
    """UserCSOTraversal trace 상태. algorithms/cso-topic-traversal.md 참조."""

    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


class ClickbaitDecision(str, Enum):
    """clickbait_module 응답. algorithms/clickbait-integration.md."""

    CLICKBAIT = "clickbait"
    CLEAN = "clean"
    ERROR = "error"


class CollectionJobStatus(str, Enum):
    """CollectionJob.status. SKIPPED = 외부 소스 비활성/조건 미충족 시.

    SKIPPED 는 docs/api/collection.md 표에서 추가 결정 (2026-05-11).
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobType(str, Enum):
    """CollectionJob.job_type + scheduler 등록 잡 종류.

    decisions.md §10 v13 + §12 A7 + §16 A9 + decision-backlog C-33·C-34·C-38·C-39·C-42.
    DAILY_COLLECT/SUMMARY_GENERATION 은 A4. INTEREST_DECAY 는 A6 daily cron
    (`app/worker/jobs/interest_decay.py`) — 베이지안 사후 시간 감쇠 + 14-day onboarding
    boost 만료 일괄 차감. LEAF_LIFECYCLE / MERGE_EVALUATION / TRACE_MERGE 는 A7 —
    leaf 신규 식별 (collection 직후 hook), 주간 leaf 병합 (월 03 UTC), 일일 trace
    merge 평가 (18 UTC). DAILY_USER_PROFILE_GENERATION 은 A8-v2 daily cron
    (`app/worker/jobs/user_profile.py`) — 19 UTC 에 사용자별 캐릭터 프로파일 +
    fusion seeds 생성, discovery slot Fusion + Reincarnation 의 input SOR.
    """

    DAILY_COLLECT = "daily_collect"
    LEAF_LIFECYCLE = "leaf_lifecycle"
    MERGE_EVALUATION = "merge_evaluation"
    SUMMARY_GENERATION = "summary_generation"
    INTEREST_DECAY = "interest_decay"
    TRACE_MERGE = "trace_merge"
    DAILY_USER_PROFILE_GENERATION = "daily_user_profile_generation"


class AdminRole(str, Enum):
    """관리자 권한. admin.md §권한 매트릭스."""

    SUPER = "super"
    OPERATOR = "operator"
    READ_ONLY = "read_only"


class UserClass(str, Enum):
    """온보딩 시 transient 사용자 분류 (User 영구 저장 X). decision-backlog P1-1."""

    UNDERGRADUATE = "undergraduate"
    RESEARCHER = "researcher"
    PROFESSOR = "professor"
    GENERAL = "general"


class TokenAudience(str, Enum):
    """JWT aud 클레임. 일반 사용자 vs 관리자 분리."""

    USER = "user"
    ADMIN = "admin"


class InterestBucket(str, Enum):
    """관심도 점수의 사용자 노출 형태. NFR-04 마스킹용."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NEUTRAL = "neutral"


class LLMProviderType(str, Enum):
    """LLMProvider 어댑터 종류. env LLM_PROVIDER 로 토글."""

    MOCK = "mock"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"
    CODEX_OAUTH = "codex_oauth"


# ============================================================
# 2. ErrorCode — docs/sdd/contracts.md §3
# ============================================================


class ErrorCode(str, Enum):
    """ErrorResponse.code 값. {area}.{specific} 점 표기.

    새 코드 추가는 본 enum + 해당 docs/api/*.md 오류 표 동시 갱신 (헌법 §6).
    """

    # --- auth ---
    AUTH_INVALID_CREDENTIALS = "auth.invalid_credentials"
    AUTH_EMAIL_TAKEN = "auth.email_taken"
    AUTH_WEAK_PASSWORD = "auth.weak_password"
    AUTH_TOKEN_EXPIRED = "auth.token_expired"
    AUTH_INVALID_TOKEN = "auth.invalid_token"
    AUTH_REFRESH_REVOKED = "auth.refresh_revoked"
    AUTH_RATE_LIMITED = "auth.rate_limited"

    # --- consent ---
    CONSENT_REQUIRED = "consent.required"
    CONSENT_ALREADY_ACTIVE = "consent.already_active"
    CONSENT_REVOCATION_PENDING = "consent.revocation_pending"
    CONSENT_DELETION_IN_PROGRESS = "consent.deletion_in_progress"

    # --- event / feedback ---
    EVENT_CONSENT_REQUIRED = "event.consent_required"
    EVENT_DUPLICATE = "event.duplicate"
    EVENT_INVALID_TARGET = "event.invalid_target"
    EVENT_BUFFER_FULL = "event.buffer_full"
    FEEDBACK_ALREADY_SAVED = "feedback.already_saved"

    # --- interest (A6) ---
    INTEREST_SYSTEM_CONFIG_MISSING = "interest.system_config_missing"

    # --- profile (A9) — daily cron 내부 오류, endpoint 부재라 응답 path 없음.
    # 본 코드는 worker 로그·메트릭 및 audit_regressions 회귀 가드에서만 사용.
    PROFILE_LLM_OUTPUT_INVALID = "profile.llm_output_invalid"
    PROFILE_BRIDGE_CSO_NOT_FOUND = "profile.bridge_cso_not_found"

    # --- onboarding ---
    ONBOARDING_CONSENT_REQUIRED = "onboarding.consent_required"
    ONBOARDING_NO_CLUSTER_SELECTED = "onboarding.no_cluster_selected"
    ONBOARDING_INVALID_CLUSTER = "onboarding.invalid_cluster"
    ONBOARDING_ALREADY_IN_PROGRESS = "onboarding.already_in_progress"
    ONBOARDING_RATE_LIMITED = "onboarding.rate_limited"

    # --- cold-start / recommendation / document ---
    COLD_START_LLM_FAILED = "cold_start.llm_failed"
    COLD_START_IN_PROGRESS = "recommendation.cold_start_in_progress"
    RECOMMENDATION_CONSENT_REQUIRED = "recommendation.consent_required"
    DOCUMENT_NOT_FOUND = "document.not_found"
    DOCUMENT_SUMMARY_UNAVAILABLE = "document.summary_unavailable"

    # --- topic ---
    TOPIC_NOT_FOUND = "topic.not_found"
    TOPIC_UNAUTHORIZED_LEAF = "topic.unauthorized_leaf"
    TOPIC_LINKAGE_ERROR = "topic.linkage_error"

    # --- leaf-lifecycle / traversal (A7) ---
    # TOPIC_LINKAGE_ERROR 는 LLM JSON parse 실패에도 재사용 (의미 동일).
    LEAF_TOPIC_NOT_FOUND = "leaf.topic_not_found"
    LEAF_TRAVERSAL_DEPTH_EXCEEDED = "traversal.path_depth_exceeded"
    LEAF_TRAVERSAL_ACTIVE_CAP_EXCEEDED = "traversal.active_cap_exceeded"
    LEAF_LLM_ANCHOR_VIOLATION = "leaf.llm_anchor_violation"
    TRACE_MERGE_CONFLICT = "traversal.merge_conflict"

    # --- collection ---
    COLLECTION_ALREADY_RUNNING = "collection.already_running"
    COLLECTION_JOB_NOT_FOUND = "collection.job_not_found"
    COLLECTION_SOURCE_DISABLED = "collection.source_disabled"
    COLLECTION_RATE_LIMITED = "collection.rate_limited"

    # --- admin ---
    ADMIN_UNAUTHORIZED = "admin.unauthorized"
    ADMIN_ROLE_INSUFFICIENT = "admin.role_insufficient"
    ADMIN_MUST_CHANGE_PASSWORD = "admin.must_change_password"
    ADMIN_REPROCESS_ALREADY_QUEUED = "admin.reprocess_already_queued"

    # --- 일반 ---
    VALIDATION_ERROR = "validation_error"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"


# ============================================================
# 3. Redis Key 컨벤션 — docs/sdd/contracts.md §4
# ============================================================


class RedisKey:
    """모든 Redis 키는 본 클래스의 static 메서드로 생성. 직접 f-string 금지.

    CI 의 scripts/check_redis_keys.py 가 raw f-string 사용을 차단한다.
    키 prefix 는 영역별 단어 — 일괄 변경·검색·CI 검증을 가능하게 함.
    """

    @staticmethod
    def refresh_token(user_id: UUID, jti: str) -> str:
        """JWT refresh token 메타. token-handling.md §refresh."""
        return f"refresh:{user_id}:{jti}"

    @staticmethod
    def refresh_index(token_hmac: str) -> str:
        """refresh token 역인덱스 (회수 처리)."""
        return f"refresh_index:{token_hmac}"

    @staticmethod
    def jwt_denylist(jti: str) -> str:
        """access token 즉시 폐기 (logout). 15m TTL."""
        return f"jwt_denylist:{jti}"

    @staticmethod
    def recommendation_cache(user_id: UUID) -> str:
        """대시보드 추천 결과 캐시. concurrency.md §2."""
        return f"recommendation:{user_id}"

    @staticmethod
    def recommendation_build_lock(user_id: UUID) -> str:
        """single-flight build lock. 30s TTL."""
        return f"lock:recommendation_build:{user_id}"

    @staticmethod
    def traversal_lock(user_id: UUID) -> str:
        """trace mutation user-level mutex. 10s TTL. concurrency.md §3."""
        return f"lock:traversal:{user_id}"

    @staticmethod
    def leaf_lifecycle_lock(user_id: UUID) -> str:
        """A7 daily emerging 식별 cron 의 per-user mutex. 60s TTL (LLM 호출 대기 포함).

        collection cron 직후 hook 으로 enqueue 되어 LLM identify_emerging 호출.
        같은 사용자 동시 진행 차단. decision-backlog C-39 (A7 round 1).
        """
        return f"lock:leaf_lifecycle:{user_id}"

    @staticmethod
    def merge_evaluation_lock(user_id: UUID) -> str:
        """A7 주간 leaf 병합 cron 의 per-user mutex. 120s TTL (LLM 호출 + UPDATE 일괄).

        weekly cron `MERGE_EVALUATION_CRON` (월 03 UTC) 가 사용자별 1회 호출.
        decision-backlog C-39 (A7 round 1).
        """
        return f"lock:merge_evaluation:{user_id}"

    @staticmethod
    def trace_merge_lock(user_id: UUID) -> str:
        """A7 daily trace merge cron 의 per-user mutex. 120s TTL.

        18 UTC = 03 KST cron 이 룰 trigger (path overlap ≥3) 후 LLM 검증 호출.
        interest_decay_lock 과 별도 분리: 두 cron 이 같은 시각이라도 trace merge 는
        LLM 호출 동반이라 lock 보유 시간 길고, decay 는 read-mostly UPDATE 만.
        decision-backlog C-39 (A7 round 1).
        """
        return f"lock:trace_merge:{user_id}"

    @staticmethod
    def collection_lock(user_id: UUID) -> str:
        """일일 수집 잡 user-level lock. 동일 사용자 잡 동시 1건 강제. collection.md §비즈니스 룰."""
        return f"lock:collection:{user_id}"

    @staticmethod
    def onboarding_lock(user_id: UUID) -> str:
        """온보딩 cold-start single-flight. 30s TTL."""
        return f"lock:onboarding:{user_id}"

    @staticmethod
    def consent_active_cache(user_id: UUID) -> str:
        """동의 활성 상태 캐시. 60s TTL. concurrency.md §7."""
        return f"consent:active:{user_id}"

    @staticmethod
    def cold_start_status(request_id: UUID) -> str:
        """cold-start LLM 작업 상태 폴링용."""
        return f"cold_start:status:{request_id}"

    @staticmethod
    def rate_limit(scope: str, identity: str) -> str:
        """slowapi rate limit counter. security/rate-limiting.md."""
        return f"rl:{scope}:{identity}"

    @staticmethod
    def llm_token_usage_daily(date_str: str) -> str:
        """일일 LLM 토큰 사용량 (date_str 은 YYYY-MM-DD)."""
        return f"llm:tokens:{date_str}"

    @staticmethod
    def llm_global_active_count() -> str:
        """전역 LLM 동시 호출 카운터 — multi-worker 분산 semaphore (C-19)."""
        return "llm:active:global"

    @staticmethod
    def llm_user_active_count(user_id: UUID) -> str:
        """사용자별 LLM 동시 호출 카운터 — multi-worker 분산 semaphore (C-19)."""
        return f"llm:active:user:{user_id}"

    @staticmethod
    def account_deletion_pending(user_id: UUID) -> str:
        """계정 삭제 진행 중 lock (codex v2 #2 → C-22). 본 키 존재 시 JwtAuthMiddleware
        가 access token 도 차단해 worker 완료 전까지 personalization API 호출 봉쇄.
        consent.service.request_account_deletion 가 SET, worker 가 DEL.
        """
        return f"account_deletion:{user_id}"

    @staticmethod
    def dwell_tick_count(user_id: UUID, document_id: UUID) -> str:
        """dwell_tick 카운터 — A6 가 atomic INCR + TTL 으로 cap 4회 (≥2분) 관리.

        algorithms/interest-bayesian.md §의사 코드 + sdd/concurrency.md §6.
        TTL 은 `DWELL_TICK_CAP_TTL_SECONDS` (default 3600s) 로 자연 소멸.
        값이 `DWELL_TICK_CAP_PER_DOCUMENT` (default 4) 초과 시 베이지안 갱신 skip
        (단 active_day 와 UserEvent INSERT 는 그대로 진행 — audit log).
        """
        return f"dwell:tick:{user_id}:{document_id}"

    @staticmethod
    def event_buffer(user_id: UUID) -> str:
        """5초 batch flush 버퍼. concurrency.md §6."""
        return f"events:buffer:{user_id}"

    @staticmethod
    def system_config_cache(key: str) -> str:
        """system_config 테이블 값 캐시. lifespan startup 시 1회 SETEX.

        A6 가 도입한 `system_config` 테이블의 (interest_params, event_weights) 값을
        매 요청 DB hit 회피용으로 Redis 에 캐싱. TTL 60s — A10 admin-console 가
        PUT /admin/system-config 시 즉시 DEL 로 invalidate. read-only 경로만 본 캐시 사용.
        """
        return f"system_config:{key}"

    @staticmethod
    def interest_decay_lock(user_id: UUID) -> str:
        """A6 daily decay cron 의 per-user mutex. 10s TTL.

        traversal_lock 과 분리하는 이유: A7 의 trace mutation 과 동시 수행 가능해야
        하고 (decay 는 read-mostly + UPDATE 만), 같은 키 사용 시 A7 latency 충돌.
        """
        return f"lock:interest_decay:{user_id}"

    @staticmethod
    def event_duplicate_cache(user_id: UUID, client_request_id: str) -> str:
        """event idempotency payload-hash 캐시 (hot path).

        DB UNIQUE(user_id, client_request_id) 가 1차 SOR. 본 캐시는 응답 RTT 단축용.
        TTL `EVENT_DUPLICATE_CACHE_TTL_SECONDS` (default 24h) — UserEvent 생성 시
        SETEX, EventBuffer flush 후에도 보존되어 client retry 시 200 응답 가능.
        """
        return f"event:dup:{user_id}:{client_request_id}"

    @staticmethod
    def user_profile_generation_lock(user_id: UUID) -> str:
        """A8-v2 daily user_profile cron 의 per-user mutex. 180s TTL (LLM 호출 동반).

        19 UTC cron `app/worker/jobs/user_profile.py` 가 사용자별 1회 acquire.
        traversal_lock 과 분리: profile 생성은 read-only (active + archived trace
        조회) + INSERT ON CONFLICT (user_profile) 만이라 A7 trace mutation 과
        독립. decision-backlog C-42 (A8-v2 round 1).
        """
        return f"lock:user_profile_gen:{user_id}"

    @staticmethod
    def user_profile_cache(user_id: UUID) -> str:
        """A8-v2 UserProfile 응답 캐시. TTL 1h (engine.build_dashboard 가 SETEX).

        daily cron 완료 후 DEL 하여 다음 dashboard 요청 시 신선한 profile 로
        SETEX. recommendation.engine 의 discovery 분기에서 1회 fetch.
        """
        return f"user_profile:{user_id}"

    @staticmethod
    def cso_clusters_cache() -> str:
        """12 CSO 클러스터 응답 캐시. TTL 24h. A3 (cso-topic engine) 도입.

        `GET /topics/cso/clusters` 가 매 호출마다 BroadInterest 12행 + cso_topic
        JOIN 을 피하기 위해 본 키에 JSON 응답을 SETEX 한다. CSO 재임포트 (`make
        import-cso`) 종료 시 명시 DEL 로 invalidate. 버전 접미사 `v1` 은 CSO
        스키마 변경 시 v2 로 올려 stale 캐시 자연 만료.
        """
        return "cso:clusters:v1"


# ============================================================
# 4. Pydantic Base 모델 — docs/sdd/contracts.md §5
# ============================================================


T = TypeVar("T", bound=BaseModel)


class PageMeta(BaseModel):
    """Cursor 기반 페이지네이션 메타. api-conventions.md §6."""

    next_cursor: str | None = None
    has_more: bool
    page_size: int


class PagedResponse(BaseModel, Generic[T]):
    """모든 list 응답 envelope. naked response 의 유일한 예외."""

    items: list[T]
    meta: PageMeta


class ErrorResponse(BaseModel):
    """표준 에러 응답. api-conventions.md §7.

    code 는 ErrorCode 상수 또는 런타임 생성 코드(validation 등) 의 fallback 으로 str 허용.
    """

    code: ErrorCode | str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class TopicChip(BaseModel):
    """추천 카드/문서 상세에서 토픽 라벨 표시용. recommendation.md, topics.md."""

    topic_id: UUID
    label: str
    type: Literal["cso", "leaf"]


class CSOTopicSummary(BaseModel):
    """CSO 토픽 간단 표현. topics.md, document detail 등 공통."""

    cso_topic_id: UUID
    label: str


class DocumentSummary(BaseModel):
    """문서 카드 공통 표현 (대시보드/검색/토픽 상세). recommendation.md."""

    document_id: UUID
    title: str
    source_name: str
    source_type: SourceType
    published_at: datetime
    url: str
    related_topics: list[CSOTopicSummary]


# ============================================================
# 5. Sentinel & 시간 helper — docs/sdd/contracts.md §6 §7
# ============================================================


class SentinelSource:
    """sentinel Source 행 이름 상수. 실제 source_id (UUID) 는 시드 시점 결정 — 부팅 시 캐시.

    cold-start.md §pseudo-document 참조.
    """

    COLD_START_PSEUDO_NAME = "cold_start_pseudo"


class ActiveDayHelper:
    """Active day 차이 계산. cso-topic-traversal.md §5.

    직접 빼기 계산 금지. 본 helper 로 통일해 의미 변동 차단.
    """

    @staticmethod
    def days_idle(user_active_day_counter: int, last_event_active_day: int) -> int:
        """사용자 마지막 인터랙션 이후 idle active day 수."""
        return user_active_day_counter - last_event_active_day


__all__ = [
    "ActiveDayHelper",
    "AdminRole",
    "CSOTopicSummary",
    "ClickbaitDecision",
    "CollectionJobStatus",
    "ContentType",
    "DocumentSummary",
    "ErrorCode",
    "ErrorResponse",
    "EventType",
    "InterestBucket",
    "JobType",
    "LLMProviderType",
    "LeafTopicStatus",
    "PageMeta",
    "PagedResponse",
    "RedisKey",
    "SentinelSource",
    "SlotType",
    "SourceType",
    "TokenAudience",
    "TopicChip",
    "TraversalStatus",
    "TrustLevel",
    "UserClass",
]
