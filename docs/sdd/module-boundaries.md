# 모듈 경계와 인터페이스 계약

본 파일은 `backend/app/` 하위 모듈의 책임·입력·출력·의존을 정의한다. 후속 에이전트가 다른 모듈에 의존할 때 본 파일의 인터페이스만 참조해야 하며, 구현 디테일은 캡슐화한다. 컴포넌트 책임 개요는 [`architecture.md`](architecture.md), 시퀀스는 [`data-flow.md`](data-flow.md).

## backend/app/ 디렉토리 책임 표

| 디렉토리 | 책임 | 입력 | 출력 | 의존 |
|---|---|---|---|---|
| `app/auth/` | 회원가입·로그인·로그아웃·토큰 갱신 | email, password, refresh_token | JWT access(15분) + refresh meta in Redis | `app/db`, `app/security` |
| `app/consent/` | UserConsent 생성·조회·철회 | user_id, consent_type | UserConsent row | `app/db` |
| `app/user/` | 사용자 프로필·삭제 요청 | user_id, profile patch | User row + cascade 삭제 잡 (1차 시연: 즉시 cascade. NFR-21의 30일 grace는 미해소 항목 — `decision-backlog.md` C-2) | `app/db`, `app/auth` |
| `app/topic/` | CSOTopic + DynamicLeafTopic 조회/탐색 | topic_id, document_id | adjacent / parent / descendants / equivalent CSO 토픽 리스트 | `networkx` 캐시, `app/db` |
| `app/traversal/` | UserCSOTraversal trace 운영 (extend/retract/split/archive) + leaf 재배치 LLM 호출 | UserEvent, current trace | trace 갱신, leaf 재매핑 | `app/topic`, `app/leaf_lifecycle`, `app/llm_provider`, `app/db` |
| `app/collection/` | **(v13 라운드)** 사용자별 일일 수집 잡 디스패치 + 결과 저장. 수집 대상 토픽 = active trace path 노드 ∪ 1-hop adjacent (`app/traversal` 의존, [`../algorithms/cso-topic-traversal.md §6`](../algorithms/cso-topic-traversal.md)). LLM tool-use 검색 단일 경로. | user_id, since timestamp | Document, DocumentTopic, CollectionJob | `app/llm_provider` (search_with_tools), `app/clickbait_client` (옵션), `app/topic`, `app/traversal` |
| ~~`app/source_adapters/`~~ | **(v13 라운드 폐기, 2026-05-11)** 6 어댑터 (arXiv, OpenAlex, Semantic Scholar, DBLP, RSS, 네이버 BS4) → `LLMProvider.search_with_tools()` 단일 경로로 통합. `app/source_adapters/` 디렉토리 미생성. | ~~topic_query, since~~ | ~~List[RawDocument]~~ | ~~httpx, beautifulsoup4~~ |
| `app/clickbait_client/` | clickbait-detector 컨테이너 호출 wrapper. **(v13 라운드)** 1차 시연 default 비활성, 사용자 News 소스 명시 활성화 시만 호출 | title, body, meta | {decision, confidence} | httpx |
| `app/leaf_lifecycle/` | LifecycleEvaluator 추상 + D 하이브리드 구현 | user_id, new_documents | new DynamicLeafTopic + 상태 전이 | `app/llm_provider`, `app/db` |
| `app/interest/` | **(A6, 2026-05-17)** Beta-Bernoulli atomic UPSERT 단일 SQL + 12 partial UNIQUE + active day daily decay cron (18 UTC) + cluster + 1-hop child propagation (env `INTEREST_PROPAGATION_ENABLED` default false) + 14-day onboarding boost daily 차감 (`boost_applied_at_active_day`) + GREATEST alpha floor + bucket-sorted `/interest/state` + `SavedDocument` / `HiddenDocument` / `NotInterestedTopic` 명시 피드백 9 endpoint. 9 파일 (`bucket / config_loader / decay / idempotency / propagation / router / schemas / service / topic_distribution`) | UserEvent (flush callback), feedback 명시 액션 | UserInterestState UPSERT + 3 명시 피드백 row | `app/db`, `app/topic`, `app/events`, `app/llm_provider` (no, A8 의존) |
| `app/events/` | **(A6 신규, 2026-05-17)** UserEvent 5초 batch buffer flush + `active_day_counter` atomic 갱신 + payload-hash idempotency (200 match / 409 mismatch) + dwell_tick Redis Lua cap. 2 파일 (`buffer / active_day`). lifespan task 시작 + shutdown lock 안 `_stopped` 검사 + 즉시 flush | UserEvent | UserEvent INSERT (`pg_insert(...).on_conflict_do_nothing(...).returning(event_id)` + None-check) + `app/interest` flush callback | `app/db`, `app/interest`, redis |
| `app/recommendation/` | core/adjacent/discovery 후보 + 랭킹 + Cold-start | user_id | List[Recommendation] (10개) + RecommendationSlot | `app/topic`, `app/interest`, `app/llm_provider` |
| `app/admin/` | 관리자 인증·수집 상태·통계·재실행 | admin_id, job_id | ReprocessRequest, statistics | `app/auth` (admin role), `app/collection` |
| `app/llm_provider/` | LLMProvider 추상 + 4 구현체 | prompt, model_slot | text completion or JSON | httpx, codex-cli OAuth bridge |
| `app/security/` | bcrypt, JWT, slowapi 정책 | password / token | hashed / verified | passlib, python-jose, slowapi |
| `app/db/` | SQLAlchemy 2.x AsyncSession factory + Alembic | DSN | Session | sqlalchemy, asyncpg |

## 추상 인터페이스 시그니처

### TraversalEngine

`UserCSOTraversal` trace의 운영 (extend/retract/split/archive). 자세히는 [`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md).

```python
# app/traversal/protocol.py
class TraversalEngine(Protocol):
    async def ingest_event(
        self,
        user: User,
        event: UserEvent,
    ) -> TraversalDelta:
        """이벤트 1건을 받아 매칭되는 trace 업데이트 또는 새 trace 생성.
        반환: 어떤 operation(extend/retract/split/none)이 일어났는지의 델타.
        """
        ...

    async def evaluate_extend(
        self,
        trace: UserCSOTraversal,
        candidate_child_cso_id: UUID,
    ) -> bool:
        """자식 노드 인터랙션 임계 충족 시 LLM 검증으로 extend 결정."""
        ...

    async def evaluate_retract(
        self,
        trace: UserCSOTraversal,
    ) -> RetractPlan | None:
        """말단 노드 점수 미달 + idle 임계 시 retract 계획 (leaf 재매핑 포함)."""
        ...

    async def evaluate_split(
        self,
        trace: UserCSOTraversal,
        diverging_children: list[UUID],
    ) -> SplitPlan | None:
        """동일 부모 산하 두 자식 동시 부상 시 split 계획 (leaf 분배 포함)."""
        ...

    async def archive_if_eligible(
        self,
        trace: UserCSOTraversal,
    ) -> bool:
        """stale 누적 90 active days 초과 시 archive."""
        ...
```

### LifecycleEvaluator

D 하이브리드 vs B 배치 평가를 갈아끼우기 위한 추상화.

```python
# app/leaf_lifecycle/protocol.py
class LifecycleEvaluator(Protocol):
    async def identify_emerging(
        self,
        user_id: UUID,
        new_documents: list[Document],
        existing_leaves: list[DynamicLeafTopic],
    ) -> list[NewLeafCandidate]:
        """새 emerging 리프 후보를 반환. D 하이브리드는 LLM 호출, B는 룰 기반."""
        ...

    async def evaluate_transitions(
        self,
        user_id: UUID,
        leaves: list[DynamicLeafTopic],
        signals: LifecycleSignals,  # 7d/21d/90d 활동 + 관심 신호 카운트
    ) -> list[StateTransition]:
        """emerging→active, active→stale, stale→archived 등 룰 기반 전이."""
        ...

    async def evaluate_merges(
        self,
        user_id: UUID,
        leaves: list[DynamicLeafTopic],
    ) -> list[MergeProposal]:
        """주 1회 LLM 호출로 라벨 유사도 + 문서 Jaccard 평가."""
        ...
```

### LLMProvider

```python
# app/llm_provider/protocol.py
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model_slot: Literal["high", "medium"],
        response_format: Literal["text", "json"] = "text",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """모델 슬롯과 환경변수에 의해 실제 모델 매핑이 결정된다.
        high → 동적 리프 생성·병합 (예: gpt-5.5 xhigh)
        medium → 섹션 요약·추천 이유 (예: gpt-5.5 medium)
        """
        ...

    async def health(self) -> ProviderHealth:
        """provider 가용성 + 누적 토큰·latency 통계."""
        ...

class LLMResponse(BaseModel):
    text: str
    parsed_json: dict | None
    prompt_tokens: int
    completion_tokens: int
    model_used: str
    finish_reason: Literal["stop", "length", "tool_use"]
```

### ~~SourceAdapter~~ (v13 라운드 폐기, 2026-05-11)

본 인터페이스는 A4 Topic-driven Pivot ([`../decisions.md §10`](../decisions.md))으로 폐기. 6 source 어댑터(arXiv, OpenAlex, Semantic Scholar, DBLP, RSS, 네이버 BS4) 모두 미구현. 수집은 `LLMProvider.search_with_tools()` 단일 경로로 통일.

```python
# 폐기된 시그니처 (역사적 참고용 — 구현 안 함)
# class SourceAdapter(Protocol):
#     name: str
#     source_type: SourceType
#     async def fetch(self, topic_query, since, max_items=100) -> list[RawDocument]: ...
```

### LLMProvider.search_with_tools (v13 라운드 신규)

```python
# app/llm_provider/protocol.py — A4 가 추가
class LLMProvider(Protocol):
    # 기존 complete(...) 시그니처 보존
    ...

    async def search_with_tools(
        self,
        trace_json: dict[str, Any],   # 사용자 active trace 전체 (cluster→subtopic→leaf path)
        leaf_label: str,               # 검색 query 의 leaf 라벨
        *,
        top_n: int = 10,
        user_id: str | None = None,
    ) -> list[SearchResult]:
        """LLM 자율 query 결정 + web 검색 도구 호출 + top_n 결과. abstract 는 LLM
        self-summary (NFR-25 정합). 실패 시 LLMBudgetExceeded 또는 ProviderError raise → 
        collection orchestrator 가 CollectionJob.failure_reason 기록."""
        ...

@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    publisher_domain: str | None
    publisher_label: str | None
    published_at: datetime | None
    abstract_summary: str   # LLM self-summary, ≤200자
```

### TopicMapper (EV-03 도메인 확장 대비)

```python
# app/topic/mapper_protocol.py
class TopicMapper(Protocol):
    async def map_document(self, document: Document) -> list[TopicMatch]:
        """문서 → 상위 토픽 + 신뢰도. 1차는 CSOTopicMapper만 구현."""
        ...

    async def find_adjacent(self, topic_id: UUID, hops: int = 1) -> list[Topic]:
        ...

    async def find_descendants(self, topic_id: UUID) -> list[Topic]:
        ...
```

### ClickbaitClassifier

```python
# app/clickbait_client/protocol.py
class ClickbaitClassifier(Protocol):
    model_name: str  # "ax-4.0-light-dora"
    adapter_type: Literal["dora"]

    async def classify(
        self,
        document: Document,
    ) -> ClickbaitDecision:
        """입력은 title + body + meta. 출력은 decision={"clickbait","clean"} + confidence ∈ [0,1].
        DoRA 모듈 컨테이너 호출 wrapper.
        """
        ...
```

## 인터페이스 vs 구현체 매핑

| 인터페이스 | 1차 구현 | 추후 교체 후보 |
|---|---|---|
| `TraversalEngine` | `DefaultTraversalEngine` (trace operation 룰 + leaf 재배치 LLM) | (현재 없음) |
| `LifecycleEvaluator` | `HybridDLifecycleEvaluator` (LLM 식별/병합 + 룰 전이) | `BatchLLMLifecycleEvaluator` |
| `LLMProvider` | **`MockProvider`** (default, deterministic JSON/text fixture per prompt hash) | `OpenAIAPIProvider`, `AnthropicAPIProvider`, `OpenRouterProvider`, `CodexOAuthProvider` (local experimental) |
| ~~`SourceAdapter`~~ | ~~6 종 (arxiv, openalex, semantic_scholar, dblp, rss_generic, naver_bs4)~~ **(v13 라운드 폐기)** → `LLMProvider.search_with_tools` 단일 경로 | 향후 도메인 어댑터 도입 시 별도 결정 |
| `TopicMapper` | `CSOTopicMapper` | (EV-03 시 도메인별) |
| `ClickbaitClassifier` | `AxDoraClassifierClient` (사용자 보유 모듈) | 추후 ONNX export 또는 cloud serve |
