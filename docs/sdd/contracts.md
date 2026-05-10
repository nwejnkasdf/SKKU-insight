# Contracts — 단일 진실 공급원 (Source of Record)

본 파일은 `backend/app/contracts.py`의 명세이다. **모든 enum, error code, Redis key 컨벤션, Pydantic base 모델은 이 한 파일에 박아 두고**, 모든 모듈은 import만 한다. 새 항목 추가는 사용자 결정 후 별도 PR로만.

자세한 운영 룰은 [`agent-orchestration.md`](agent-orchestration.md). HTTP 표준은 [`api-conventions.md`](api-conventions.md). 동시성 키 디자인은 [`concurrency.md`](concurrency.md).

## 1. 위치와 import 패턴

```python
# backend/app/contracts.py — SOR
from enum import Enum
from typing import Generic, TypeVar
from uuid import UUID
from pydantic import BaseModel

# 모든 모듈은 다음과 같이 import:
# from app.contracts import EventType, ErrorCode, RedisKey, PageMeta, PagedResponse
```

## 2. Enum

```python
class EventType(str, Enum):
    VIEW = "view"
    CLICK = "click"
    DWELL_TICK = "dwell_tick"
    OPEN_EXTERNAL = "open_external"
    SAVE = "save"
    HIDE = "hide"
    NOT_INTERESTED = "not_interested"


class ContentType(str, Enum):
    ACADEMIC_PAPER = "academic_paper"
    VENDOR_BLOG = "vendor_blog"
    TECH_NEWS = "tech_news"
    PSEUDO_COLD_START = "pseudo_cold_start"


class SourceType(str, Enum):
    ACADEMIC = "academic"
    VENDOR_BLOG = "vendor_blog"
    TECH_NEWS = "tech_news"


class TrustLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SlotType(str, Enum):
    CORE = "core"
    ADJACENT = "adjacent"
    DISCOVERY = "discovery"
    FALLBACK_ADJACENT = "fallback_adjacent"
    FALLBACK_TREND = "fallback_trend"


class LeafTopicStatus(str, Enum):
    EMERGING = "emerging"
    ACTIVE = "active"
    STALE = "stale"
    MERGED = "merged"
    ARCHIVED = "archived"


class TraversalStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


class ClickbaitDecision(str, Enum):
    CLICKBAIT = "clickbait"
    CLEAN = "clean"
    ERROR = "error"


class CollectionJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"   # 외부 소스 비활성/조건 미충족 (2026-05-11 추가)


class AdminRole(str, Enum):
    SUPER = "super"
    OPERATOR = "operator"
    READ_ONLY = "read_only"


class UserClass(str, Enum):
    UNDERGRADUATE = "undergraduate"
    RESEARCHER = "researcher"
    PROFESSOR = "professor"
    GENERAL = "general"


class TokenAudience(str, Enum):
    USER = "user"
    ADMIN = "admin"


class InterestBucket(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NEUTRAL = "neutral"


class LLMProviderType(str, Enum):
    MOCK = "mock"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"
    CODEX_OAUTH = "codex_oauth"
```

> 새 enum 값 추가는 본 파일 + alembic CHECK 제약 + docs/data/schema.md + docs/api/*.md 같은 4곳을 동시 수정해야 한다. 한 곳만 수정 금지.

## 3. Error Code

```python
class ErrorCode(str, Enum):
    # auth
    AUTH_INVALID_CREDENTIALS = "auth.invalid_credentials"
    AUTH_EMAIL_TAKEN = "auth.email_taken"
    AUTH_WEAK_PASSWORD = "auth.weak_password"
    AUTH_TOKEN_EXPIRED = "auth.token_expired"
    AUTH_INVALID_TOKEN = "auth.invalid_token"
    AUTH_REFRESH_REVOKED = "auth.refresh_revoked"
    AUTH_RATE_LIMITED = "auth.rate_limited"
    # consent
    CONSENT_REQUIRED = "consent.required"
    CONSENT_ALREADY_ACTIVE = "consent.already_active"
    CONSENT_REVOCATION_PENDING = "consent.revocation_pending"
    CONSENT_DELETION_IN_PROGRESS = "consent.deletion_in_progress"
    # event / feedback
    EVENT_CONSENT_REQUIRED = "event.consent_required"
    EVENT_DUPLICATE = "event.duplicate"
    EVENT_INVALID_TARGET = "event.invalid_target"
    FEEDBACK_ALREADY_SAVED = "feedback.already_saved"
    # onboarding
    ONBOARDING_CONSENT_REQUIRED = "onboarding.consent_required"
    ONBOARDING_NO_CLUSTER_SELECTED = "onboarding.no_cluster_selected"
    ONBOARDING_INVALID_CLUSTER = "onboarding.invalid_cluster"
    ONBOARDING_ALREADY_IN_PROGRESS = "onboarding.already_in_progress"
    ONBOARDING_RATE_LIMITED = "onboarding.rate_limited"
    # cold-start
    COLD_START_LLM_FAILED = "cold_start.llm_failed"
    COLD_START_IN_PROGRESS = "recommendation.cold_start_in_progress"
    # recommendation
    RECOMMENDATION_CONSENT_REQUIRED = "recommendation.consent_required"
    DOCUMENT_NOT_FOUND = "document.not_found"
    DOCUMENT_SUMMARY_UNAVAILABLE = "document.summary_unavailable"
    # topic
    TOPIC_NOT_FOUND = "topic.not_found"
    TOPIC_UNAUTHORIZED_LEAF = "topic.unauthorized_leaf"
    TOPIC_LINKAGE_ERROR = "topic.linkage_error"
    # collection (사용자 영역 + 관리자 영역 공유)
    COLLECTION_ALREADY_RUNNING = "collection.already_running"
    COLLECTION_JOB_NOT_FOUND = "collection.job_not_found"
    COLLECTION_SOURCE_DISABLED = "collection.source_disabled"
    COLLECTION_RATE_LIMITED = "collection.rate_limited"
    # admin
    ADMIN_UNAUTHORIZED = "admin.unauthorized"
    ADMIN_ROLE_INSUFFICIENT = "admin.role_insufficient"
    ADMIN_MUST_CHANGE_PASSWORD = "admin.must_change_password"
    ADMIN_REPROCESS_ALREADY_QUEUED = "admin.reprocess_already_queued"
    # 일반
    VALIDATION_ERROR = "validation_error"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"
```

코드 단어는 `{area}.{specific}` 점 표기. 새 코드 추가는 본 파일 + 해당 docs/api/*.md 오류 표 동시 수정.

## 4. Redis Key 컨벤션

```python
class RedisKey:
    """모든 Redis 키는 본 클래스의 static 메서드로 생성. 직접 f-string 금지."""

    @staticmethod
    def refresh_token(user_id: UUID, jti: str) -> str:
        return f"refresh:{user_id}:{jti}"

    @staticmethod
    def refresh_index(token_hmac: str) -> str:
        return f"refresh_index:{token_hmac}"

    @staticmethod
    def jwt_denylist(jti: str) -> str:
        return f"jwt_denylist:{jti}"

    @staticmethod
    def recommendation_cache(user_id: UUID) -> str:
        return f"recommendation:{user_id}"

    @staticmethod
    def recommendation_build_lock(user_id: UUID) -> str:
        return f"lock:recommendation_build:{user_id}"

    @staticmethod
    def traversal_lock(user_id: UUID) -> str:
        return f"lock:traversal:{user_id}"

    @staticmethod
    def collection_lock(user_id: UUID) -> str:
        # 일일 수집 잡 user-level lock. 동일 사용자 잡 동시 1건 강제.
        return f"lock:collection:{user_id}"

    @staticmethod
    def onboarding_lock(user_id: UUID) -> str:
        return f"lock:onboarding:{user_id}"

    @staticmethod
    def consent_active_cache(user_id: UUID) -> str:
        return f"consent:active:{user_id}"

    @staticmethod
    def cold_start_status(request_id: UUID) -> str:
        return f"cold_start:status:{request_id}"

    @staticmethod
    def rate_limit(scope: str, identity: str) -> str:
        return f"rl:{scope}:{identity}"

    @staticmethod
    def llm_token_usage_daily(date_str: str) -> str:
        return f"llm:tokens:{date_str}"

    @staticmethod
    def dwell_tick_count(user_id: UUID, document_id: UUID) -> str:
        # 단 atomic SQL UPSERT 사용 시 별도 Redis 키 불필요.
        # 본 함수는 만약 Redis 카운터 기반으로 갈 경우 대비.
        return f"dwell:{user_id}:{document_id}"

    @staticmethod
    def event_buffer(user_id: UUID) -> str:
        return f"events:buffer:{user_id}"
```

> 키 prefix는 모두 영역별 단어. 직접 f-string 금지 — 검색·일괄 변경·CI 검증을 가능하게 함.

## 5. Pydantic Base 모델

```python
T = TypeVar("T", bound=BaseModel)


class PageMeta(BaseModel):
    next_cursor: str | None = None
    has_more: bool
    page_size: int


class PagedResponse(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class ErrorResponse(BaseModel):
    code: ErrorCode | str   # str fallback for runtime-generated codes (validation 등)
    message: str
    details: dict | None = None
    request_id: str | None = None


class TopicChip(BaseModel):
    topic_id: UUID
    label: str
    type: Literal["cso", "leaf"]


class CSOTopicSummary(BaseModel):
    cso_topic_id: UUID
    label: str


class DocumentSummary(BaseModel):
    document_id: UUID
    title: str
    source_name: str
    source_type: SourceType
    published_at: datetime
    url: str
    related_topics: list[CSOTopicSummary]
```

본 base 모델들을 다른 영역 schema가 import해서 재사용. 같은 의미인데 모듈마다 다른 클래스 정의 금지.

## 6. SourceID 상수 (sentinel)

```python
class SentinelSource:
    """sentinel Source 행은 schema.md 시드에서 INSERT.
    실제 source_id (UUID)는 시드 시점에 결정되므로 환경변수로 read."""
    COLD_START_PSEUDO_NAME = "cold_start_pseudo"
```

`source_id`는 UUID이므로 코드에서 직접 상수 X. `Source.name == SentinelSource.COLD_START_PSEUDO_NAME` 으로 query하거나 부팅 시 캐시. cold-start.md §pseudo-document 참고.

## 7. 시간 단위 helper

```python
class ActiveDayHelper:
    """Active day 차이 계산. cso-topic-traversal.md §5 참고."""

    @staticmethod
    def days_idle(user_active_day_counter: int, last_event_active_day: int) -> int:
        return user_active_day_counter - last_event_active_day
```

직접 빼기 계산 금지. 본 helper로 통일해 의미 변동 차단.

## 8. 변경 정책

| 변경 종류 | 절차 |
|---|---|
| 새 enum 값 | contracts.py + alembic CHECK + docs/data/schema.md + docs/api/*.md 동시 PR. 사용자 승인 후 머지. |
| 새 error code | contracts.py + 해당 docs/api/*.md 오류 표 동시 PR |
| 새 Redis key | contracts.py + docs/sdd/concurrency.md key 디자인 동시 PR |
| 새 base 모델 | contracts.py + 해당 영역 schema 동시 PR |
| 기존 항목 rename | breaking change — 모든 사용처 동시 갱신 + CI 통과 필수 |

## 9. CI 검증

`scripts/check_contracts.py` (CI 강제):

- contracts.py의 enum 값이 alembic migration의 CHECK 제약과 일치
- ErrorCode 값이 docs/api/*.md 의 모든 오류 표에 정의
- RedisKey 메서드가 모든 Redis 호출 위치에서 사용 (raw f-string 금지)
- contracts.py 변경 시 OpenAPI codegen 자동 재실행
