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
    FEEDBACK_ALREADY_SAVED = "feedback.already_saved"

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
        """dwell_tick 카운터 (atomic SQL UPSERT 사용 시 보통 불필요)."""
        return f"dwell:{user_id}:{document_id}"

    @staticmethod
    def event_buffer(user_id: UUID) -> str:
        """5초 batch flush 버퍼. concurrency.md §6."""
        return f"events:buffer:{user_id}"

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
