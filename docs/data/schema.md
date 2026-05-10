# 데이터 스키마 (SQLAlchemy 2.x 의사 모델)

본 파일은 SRS Data Dictionary 21개 엔티티를 SQLAlchemy 2.x async ORM으로 옮긴 의사 모델이다. 인덱스, 제약, FK CASCADE 룰을 명시한다. ERD는 [`erd.mmd`](erd.mmd). SRS 원본 §3.4은 [`../srs/04-data-model.md`](../srs/04-data-model.md).

본 문서는 Alembic migration 작성을 위한 사양이다. 컬럼 타입, nullable, default, unique, FK ondelete를 정확히 따른다.

## 공통 규칙

- `id` 필드는 `UUID` (`postgresql.UUID(as_uuid=True)`, default `uuid.uuid4`)
- 시간 컬럼은 `TIMESTAMPTZ` (`DateTime(timezone=True)`), default = server side `func.now()`
- 모든 엔티티에 `created_at`은 기본 포함, `updated_at`은 mutating 엔티티에만
- 사용자 종속 데이터의 FK는 `ondelete="CASCADE"`. **1차 시연은 즉시 cascade로 진행** — NFR-21이 명시한 "30일 grace period"는 별도 worker로 soft delete + 지연 cascade 패턴이 필요하나 1차 범위에서 미구현 ([`../decision-backlog.md`](../decision-backlog.md) C-2). 시연 후 폴리시 단계에서 보강.
- soft delete가 필요한 곳은 `deleted_at` 컬럼 (User만)

## 엔티티

### User

```python
class User(Base):
    __tablename__ = "user"
    user_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # email 은 **lowercase + trim 정규화 후 저장**. 3겹 방어 (auth-flow.md):
    #   1) Pydantic validator (요청 경계)
    #   2) service 계층 (방어적, CLI/시드도 통과)
    #   3) DB functional index `LOWER(email)` partial UNIQUE (raw SQL bypass 차단)
    # 대소문자·양끝 공백 변형 우회 차단. column 자체는 case-preserved storage X — 정규화된 값만 저장.
    email: Mapped[str] = mapped_column(String(320), index=False, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    # Active day counter — wallclock 일수가 아니라 "사용자 인터랙션이 1건이라도 있는 날"의 단조증가 카운터.
    # 모든 시간 종속 라이프사이클 (trace, leaf, 베이지안 감쇠)이 본 카운터 단위로 N-day 임계 평가.
    # 자세히는 algorithms/cso-topic-traversal.md §5.
    active_day_counter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_active_calendar_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 로그인 성공 시 갱신 (auth-flow.md §2 시퀀스, api/auth.md §비즈니스 룰).
    # 본 컬럼은 decision-backlog C-10 으로 schema.md 에 추가됨 (A2 2026-05-11).
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

인덱스(Alembic raw DDL):

```sql
CREATE UNIQUE INDEX ix_user_email
  ON "user" (LOWER(email))
  WHERE deleted_at IS NULL;
```

functional index 사유: 클라이언트가 `Test@TEST.com` ↔ `test@test.com` 으로 중복 가입을 시도해도 차단. service/Pydantic 정규화가 실수로 빠져도 DB 단에서 거름. 조회도 항상 `WHERE LOWER(email) = :normalized_email` 패턴 사용.

### AdminUser

```python
class AdminUser(Base):
    __tablename__ = "admin_user"
    admin_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)   # super | operator | read_only
    status: Mapped[str] = mapped_column(String(20), default="active")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

CHECK (`role IN ('super','operator','read_only')`).

### UserConsent

```python
class UserConsent(Base):
    __tablename__ = "user_consent"
    consent_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), index=True, nullable=False)
    consent_type: Mapped[str] = mapped_column(String(40), nullable=False)   # personalization
    agreed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

복합 인덱스: `(user_id, consent_type)`. `revoked_at IS NULL`로 활성 동의 조회.

### BroadInterest

```python
class BroadInterest(Base):
    __tablename__ = "broad_interest"
    broad_interest_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cso_cluster_label: Mapped[str] = mapped_column(String(40), nullable=False)   # 12 cluster
    cso_seed_topic_id: Mapped[UUID] = mapped_column(
        ForeignKey("cso_topic.cso_topic_id", ondelete="RESTRICT"), nullable=False,
    )   # 클러스터 seed CSO topic — onboarding prior boost 진입점 (api/onboarding.md)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
```

12 행 시드. cso_cluster_label은 `algorithms/cso-mapping.md`의 12 클러스터 라벨 (AI, Systems, ...).
`cso_seed_topic_id` 는 cso-mapping.md SEEDS dict 의 cluster→full label 매핑을 시드 시점에 cso_topic FK 로 resolve.

### CSOTopic

```python
class CSOTopic(Base):
    __tablename__ = "cso_topic"
    cso_topic_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    uri: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    parent_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"))
    cluster_labels: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
```

인덱스: `(parent_topic_id)`, `GIN(cluster_labels)`. 그래프 사이클 금지 (앱 레벨 검증).

### DynamicLeafTopic

```python
class DynamicLeafTopic(Base):
    __tablename__ = "dynamic_leaf_topic"
    leaf_topic_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    label_en: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)   # emerging|active|stale|merged|archived
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # last_seen_at (wallclock) 은 active day 모델로 대체됨 — last_signal_active_day 사용 (decisions.md §시간 단위).
    # Active day 기반 라이프사이클 (algorithms/cso-topic-traversal.md §5, leaf-topic-lifecycle.md)
    created_active_day: Mapped[int] = mapped_column(Integer, nullable=False)
    last_signal_active_day: Mapped[int] = mapped_column(Integer, nullable=False)
    merged_into_leaf_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="SET NULL"))
```

CHECK (`status IN ('emerging','active','stale','merged','archived')`). 인덱스: `(user_id, status)`. Active day 임계 평가는 `user.active_day_counter - last_signal_active_day` 차이 기반.

### UserCSOTraversal

> 사용자 × CSO 토픽 traversal trace 모델. **사용자 관심은 단일 노드가 아니라 path 자체가 하나의 관심 상태 객체**라는 본 시스템의 핵심 모델. 자세히는 [`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md).

```python
class UserCSOTraversal(Base):
    __tablename__ = "user_cso_traversal"
    trace_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), index=True, nullable=False)
    # path: ordered list of cso_topic_id, root → 말단. UUID 배열로 저장
    path: Mapped[list[UUID]] = mapped_column(ARRAY(postgresql.UUID(as_uuid=True)), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)   # active|stale|archived
    started_active_day: Mapped[int] = mapped_column(Integer, nullable=False)
    last_activity_active_day: Mapped[int] = mapped_column(Integer, nullable=False)
    score_tail: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)   # path 끝 노드 베이지안 사후 평균 캐시
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

CHECK (`status IN ('active','stale','archived')`). CHECK (`array_length(path, 1) >= 1`). 인덱스: `(user_id, status)`, `GIN(path)` (path 위 cso_topic 검색용). path 최대 길이는 앱 레벨 cap 8 ([`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md) §11).

> **Trace operation 시 무결성**: trace_id의 path 변경(extend/retract/split)은 항상 `last_activity_active_day = user.active_day_counter` 동시 갱신. 자세한 룰은 [`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md) §3.

### DynamicLeafTopicCSOTopic

```python
class DynamicLeafTopicCSOTopic(Base):
    __tablename__ = "dynamic_leaf_topic_cso_topic"
    leaf_topic_id: Mapped[UUID] = mapped_column(ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="CASCADE"), primary_key=True)
    cso_topic_id: Mapped[UUID] = mapped_column(ForeignKey("cso_topic.cso_topic_id", ondelete="RESTRICT"), primary_key=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

FR-16 보장: leaf_topic_id에 대해 행이 최소 1건 존재 — 앱 레벨 invariant.

### UserInterestState

```python
class UserInterestState(Base):
    __tablename__ = "user_interest_state"
    state_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), nullable=False, index=True)
    cso_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"))
    leaf_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="CASCADE"))
    long_alpha: Mapped[float] = mapped_column(Float, nullable=False)
    long_beta: Mapped[float] = mapped_column(Float, nullable=False)
    short_alpha: Mapped[float] = mapped_column(Float, nullable=False)
    short_beta: Mapped[float] = mapped_column(Float, nullable=False)
    long_score: Mapped[float] = mapped_column(Float, nullable=False)   # 사후 평균 캐시
    short_score: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # Active day 기반 시간 감쇠 (algorithms/interest-bayesian.md §시간 감쇠)
    last_event_active_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_decay_active_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

> ⚠️ **NULL 의미론 보완**: surrogate `state_id` PK는 그대로 두되, 일반 `UNIQUE(user_id, cso_topic_id, leaf_topic_id)`는 NULL ≠ NULL 의미론 때문에 중복 방지를 못 한다. 따라서 케이스별 partial UNIQUE INDEX로 표현.

```sql
ALTER TABLE user_interest_state
  ADD CONSTRAINT ck_user_interest_state_at_least_one_topic
  CHECK (cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL);

CREATE UNIQUE INDEX ux_user_interest_state_cso_only
  ON user_interest_state (user_id, cso_topic_id)
  WHERE leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL;

CREATE UNIQUE INDEX ux_user_interest_state_leaf_only
  ON user_interest_state (user_id, leaf_topic_id)
  WHERE cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL;

CREATE UNIQUE INDEX ux_user_interest_state_pair
  ON user_interest_state (user_id, cso_topic_id, leaf_topic_id)
  WHERE cso_topic_id IS NOT NULL AND leaf_topic_id IS NOT NULL;
```

### SourcePolicy

```python
class SourcePolicy(Base):
    __tablename__ = "source_policy"
    policy_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_category: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)   # academic | vendor_blog | tech_news
    trust_level: Mapped[str] = mapped_column(String(10), nullable=False)
    collection_rule: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
```

### Source

```python
class Source(Base):
    __tablename__ = "source"
    source_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(10), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)   # cso_tags, language, parser_id 등
```

### CollectionJob

```python
class CollectionJob(Base):
    __tablename__ = "collection_job"
    job_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), index=True)
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("source.source_id", ondelete="SET NULL"))
    target_cso_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"))
    target_leaf_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="SET NULL"))
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

인덱스: `(status, finished_at DESC)`, `(user_id, finished_at DESC)`.

### Document

```python
class Document(Base):
    __tablename__ = "document"
    document_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("source.source_id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_title: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(1000))
    doi: Mapped[str | None] = mapped_column(String(120), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # content_type ∈ ContentType enum (sdd/contracts.md §2): academic_paper | vendor_blog | tech_news | pseudo_cold_start
    # Source.source_type → Document.content_type 매핑 (수집 단계):
    #   source_type=academic     → content_type=academic_paper
    #   source_type=vendor_blog  → content_type=vendor_blog
    #   source_type=tech_news    → content_type=tech_news
    #   sentinel cold_start_pseudo Source → content_type=pseudo_cold_start (algorithms/cold-start.md §pseudo-document)
    language: Mapped[str | None] = mapped_column(String(10))
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

UNIQUE: `(canonical_url)` (NULL 허용 partial), UNIQUE: `(doi)` (NULL 허용 partial). 중복 제거는 `algorithms/recommendation-ranking.md` + `collection` 모듈에서 (URL/DOI/normalized_title + Levenshtein).

### DocumentTopic

> ⚠️ **수정 사유**: 초기 안은 `(document_id, cso_topic_id, leaf_topic_id)` 복합 PK였으나 후 두 컬럼이 `nullable=True`라서 PostgreSQL의 PK = NOT NULL 강제와 충돌. 또한 일반 UNIQUE 제약은 `NULL ≠ NULL` 의미론으로 NULL 포함 조합의 중복을 막지 못한다. **surrogate UUID PK + 케이스별 partial UNIQUE INDEX** 패턴으로 수정.

```python
class DocumentTopic(Base):
    __tablename__ = "document_topic"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("document.document_id", ondelete="CASCADE"), nullable=False, index=True)
    cso_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"), nullable=True)
    leaf_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="CASCADE"), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
```

제약 (alembic 마이그레이션에서 추가):

```sql
-- 최소 한쪽은 NOT NULL
ALTER TABLE document_topic
  ADD CONSTRAINT ck_document_topic_at_least_one_topic
  CHECK (cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL);

-- 케이스별 partial UNIQUE (중복 매핑 방지)
CREATE UNIQUE INDEX ux_document_topic_cso_only
  ON document_topic (document_id, cso_topic_id)
  WHERE leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL;

CREATE UNIQUE INDEX ux_document_topic_leaf_only
  ON document_topic (document_id, leaf_topic_id)
  WHERE cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL;

CREATE UNIQUE INDEX ux_document_topic_pair
  ON document_topic (document_id, cso_topic_id, leaf_topic_id)
  WHERE cso_topic_id IS NOT NULL AND leaf_topic_id IS NOT NULL;
```

### ClickbaitResult

```python
class ClickbaitResult(Base):
    __tablename__ = "clickbait_result"
    result_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("document.document_id", ondelete="CASCADE"), unique=True, index=True)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(20), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)   # clickbait | clean | error
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

### RecommendationSlot

```python
class RecommendationSlot(Base):
    __tablename__ = "recommendation_slot"
    slot_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), index=True)
    slot_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(255))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
```

### Recommendation

```python
class Recommendation(Base):
    __tablename__ = "recommendation"
    recommendation_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), index=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("document.document_id", ondelete="CASCADE"))
    slot_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    score: Mapped[float | None] = mapped_column(Float)   # 관리자 콘솔에만 노출 (NFR-04)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
```

UNIQUE(user_id, document_id, slot_type, created_at::date) — 같은 일자에 동일 문서 중복 추천 방지 (FR-28).

### UserEvent

```python
class UserEvent(Base):
    __tablename__ = "user_event"
    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), index=True)
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("document.document_id", ondelete="SET NULL"))
    cso_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"))
    leaf_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    dwell_ms: Mapped[int | None] = mapped_column(Integer)
    client_request_id: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

UNIQUE(user_id, client_request_id) — idempotency.

### SavedDocument

```python
class SavedDocument(Base):
    __tablename__ = "saved_document"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("document.document_id", ondelete="CASCADE"), primary_key=True)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

### HiddenDocument

```python
class HiddenDocument(Base):
    __tablename__ = "hidden_document"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("document.document_id", ondelete="CASCADE"), primary_key=True)
    hidden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

### NotInterestedTopic

> ⚠️ **수정 사유**: DocumentTopic과 동일한 nullable composite PK 충돌. 같은 패턴(surrogate PK + 케이스별 partial UNIQUE INDEX)으로 수정.

```python
class NotInterestedTopic(Base):
    __tablename__ = "not_interested_topic"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.user_id", ondelete="CASCADE"), nullable=False, index=True)
    cso_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"), nullable=True)
    leaf_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

제약 (alembic 마이그레이션에서 추가):

```sql
ALTER TABLE not_interested_topic
  ADD CONSTRAINT ck_not_interested_topic_at_least_one_topic
  CHECK (cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL);

CREATE UNIQUE INDEX ux_not_interested_topic_cso_only
  ON not_interested_topic (user_id, cso_topic_id)
  WHERE leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL;

CREATE UNIQUE INDEX ux_not_interested_topic_leaf_only
  ON not_interested_topic (user_id, leaf_topic_id)
  WHERE cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL;

CREATE UNIQUE INDEX ux_not_interested_topic_pair
  ON not_interested_topic (user_id, cso_topic_id, leaf_topic_id)
  WHERE cso_topic_id IS NOT NULL AND leaf_topic_id IS NOT NULL;
```

### ReprocessRequest

```python
class ReprocessRequest(Base):
    __tablename__ = "reprocess_request"
    request_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[UUID] = mapped_column(ForeignKey("admin_user.admin_id", ondelete="RESTRICT"))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("collection_job.job_id", ondelete="CASCADE"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(20), nullable=False)   # queued | running | succeeded | failed
    result_message: Mapped[str | None] = mapped_column(Text)
```

## 추가 테이블 (TopicLinkageError, FR-64)

SRS Data Dictionary에 명시되진 않았지만 FR-64 (토픽 연결 오류 표시)를 위해 별도 테이블 생성.

```python
class TopicLinkageError(Base):
    __tablename__ = "topic_linkage_error"
    error_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("document.document_id", ondelete="CASCADE"))
    expected_cso_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"))
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

## 네이버뉴스 cascade 룰

결정 매트릭스 §5에 따라 네이버뉴스 IT/과학 크롤러가 만든 Document는 토픽 매핑이 부모. 토픽 삭제 시 cascade. 본 스키마에서는 `Document.source_id → Source` (RESTRICT)이지만 Document → DocumentTopic → CSOTopic 경로는 CASCADE이므로 토픽 삭제 → DocumentTopic CASCADE → 연결 해제. 만약 모든 토픽 매핑이 사라진 네이버뉴스 Document가 남으면 야간 정리 잡이 삭제. <!-- TODO: A4가 정리 잡 정책을 결정 -->

## 시드 데이터

- BroadInterest: 12 행 (cluster 매핑) — **A3가 CSO 임포트 후 시드** (`cso_seed_topic_id` FK 의존). A2 마이그레이션은 빈 테이블만 생성.
- SourcePolicy: **3 행 시드 정확값** (A2 마이그레이션이 op.bulk_insert):

  | source_category | trust_level | collection_rule | enabled |
  |---|---|---|---|
  | `academic` | `high` | `{}` | `true` |
  | `vendor_blog` | `high` | `{}` | `true` |
  | `tech_news` | `medium` | `{}` | `true` |

  trust_level 분포는 [`sources-registry.md`](sources-registry.md)의 trust 분포에서 추론. `collection_rule={}`는 1차 시드의 placeholder — A4가 운영 단계에서 보강.
- Source: ~50 행 ([`sources-registry.md`](sources-registry.md)) — A4가 시드 / **+ A2 마이그레이션이 sentinel 1 행** (`name="cold_start_pseudo"`, `enabled=false`, `source_type="vendor_blog"`, `url="internal://cold-start-pseudo"`, `trust_level="low"`) — pseudo cold-start Document의 FK 충족용. 자세히는 [`../algorithms/cold-start.md §pseudo-document`](../algorithms/cold-start.md)
- AdminUser: 1 행 (`scripts/create_admin.py`로 생성, [`../ops/admin-bootstrap.md`](../ops/admin-bootstrap.md))
- UserCSOTraversal: 시드 페르소나 사용 시 페르소나별 1~3 trace 생성, 14일치 인터랙션과 함께 path 시뮬레이션 ([`seed-personas.md`](seed-personas.md))

## Active day 운영 노트

`User.active_day_counter`는 `algorithms/cso-topic-traversal.md §5` 알고리즘의 단조증가 카운터. 사용자가 그날 첫 인터랙션 이벤트(`UserEvent`) 처리 시 **atomic SQL UPDATE로 갱신**해야 한다 (동시 이벤트 race 방어, [`../sdd/concurrency.md §4.2`](../sdd/concurrency.md)).

```python
async def maybe_increment_active_day(user_id: UUID, today: date) -> int:
    """오늘 첫 인터랙션이면 +1. 동시 두 이벤트가 들어와도 첫 번째만 +1."""
    result = await session.execute(
        text("""
            UPDATE "user"
            SET active_day_counter = active_day_counter + 1,
                last_active_calendar_date = :today
            WHERE user_id = :user_id
              AND (last_active_calendar_date IS NULL OR last_active_calendar_date < :today)
            RETURNING active_day_counter
        """),
        {"user_id": user_id, "today": today},
    )
    row = result.first()
    if row:
        return row.active_day_counter
    # 이미 오늘 카운트됨
    return await session.scalar(
        text("SELECT active_day_counter FROM \"user\" WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
```

`WHERE last_active_calendar_date < :today` 조건이 가드. 동시 두 호출 중 첫 번째만 row 갱신, 두 번째는 0건 갱신 후 현재 값 read.

이후 모든 시간 종속 라이프사이클 평가는 `user.active_day_counter` 단위 차이로 수행한다. **베이지안 사후 update와 dwell_tick cap도 모두 atomic SQL 패턴 사용** ([`../algorithms/interest-bayesian.md`](../algorithms/interest-bayesian.md), [`../sdd/concurrency.md §4.1`](../sdd/concurrency.md)).
