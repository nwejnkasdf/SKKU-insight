# API: Topics

본 파일은 CSO 상위 토픽과 사용자별 동적 리프 토픽 조회·탐색 API 명세이다. 관련 FR: FR-09, FR-12, FR-13, FR-14, FR-15, FR-16, FR-46, FR-47, FR-48. 관련 NFR: NFR-08. 토픽 구조와 그래프 알고리즘은 [`../algorithms/cso-mapping.md`](../algorithms/cso-mapping.md), traversal trace는 [`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md).

> **API 통신 규약**: [`../sdd/api-conventions.md`](../sdd/api-conventions.md) 따름. list endpoint는 §6 cursor + limit + PagedResponse envelope.

## 베이스

- 기본 경로: `/topics`
- 인증: 모든 GET은 access_token + 동의 활성 사용자

## 엔드포인트 표

| Method | Path | 설명 |
|---|---|---|
| GET | `/topics/cso/clusters` | 12 CSO 클러스터 (온보딩·설정 선택지 공통) |
| GET | `/topics/cso/{cso_topic_id}` | CSO 토픽 상세 + 부모 |
| GET | `/topics/cso/{cso_topic_id}/adjacent?hops=1` | 인접 CSO 토픽 |
| GET | `/topics/cso/{cso_topic_id}/descendants` | 후손 CSO 토픽 |
| GET | `/topics/leaves?status=active` | 자기 동적 리프 토픽 목록 (PagedResponse) |
| GET | `/topics/leaves/{leaf_topic_id}` | 동적 리프 상세 + 연결된 CSO + 최근 문서 |
| GET | `/topics/{topic_id}/documents?since=...` | 토픽 상세 화면 문서 (UI-03, PagedResponse) |
| GET | `/topics/traces?status=active` | 자기 traversal trace 목록 (디버그·설정용) |
| GET | `/topics/traces/{trace_id}` | trace 상세 — path 위 노드 + 산하 leaf |

## 스키마

```python
TopicType = Literal["cso", "leaf"]
LeafStatus = Literal["emerging", "active", "stale", "merged", "archived"]

class CSOCluster(BaseModel):
    cso_topic_id: UUID
    label: str            # "Artificial Intelligence", "Software Engineering", ...
    description_ko: str
    document_count: int   # 최근 30일 통계

class CSOTopicDetail(BaseModel):
    cso_topic_id: UUID
    label: str
    uri: str              # CSO 원본 URI
    # A3 결정 18 + 자체감사 A-4: deprecated `parent_topic_id` 미노출. cso_topic_parent M:N SOR 만.
    parents: list[CSOTopicSummary]   # 다중 부모 자연 표현 (CSO 는 DAG)
    children_count: int

class CSOTopicSummary(BaseModel):
    cso_topic_id: UUID
    label: str

class AdjacentResponse(BaseModel):
    seed_id: UUID
    hops: int
    topics: list[CSOTopicSummary]

class DynamicLeafTopic(BaseModel):
    leaf_topic_id: UUID
    label: str
    confidence: float       # ∈ [0,1]
    status: LeafStatus
    created_at: datetime
    cso_topic_ids: list[UUID]
    merged_into_leaf_topic_id: UUID | None

class TopicDocumentsResponse(BaseModel):
    topic_type: TopicType
    topic_id: UUID
    items: list[DocumentSummary]    # PagedResponse envelope (api-conventions.md §6)
    meta: PageMeta                   # next_cursor, has_more, page_size

class TraversalTraceSummary(BaseModel):
    trace_id: UUID
    path_labels: list[str]           # path 위 cso_topic_id 라벨 (root → tail)
    status: Literal["active", "stale", "archived"]
    started_active_day: int
    last_activity_active_day: int
    leaf_count: int                  # 산하 active leaf 수

class TraversalTraceDetail(BaseModel):
    trace_id: UUID
    path: list[CSOTopicSummary]      # ordered, root → tail
    status: Literal["active", "stale", "archived"]
    leaves: list[DynamicLeafTopic]   # path 위 노드에 매핑된 active leaf
    started_active_day: int
    last_activity_active_day: int
    score_tail: float | None = None  # 일반 사용자 응답에서 null (NFR-04 마스킹, A3 결정 7). 관리자 endpoint 만 실제 값.

# DocumentSummary 는 contracts.py 의 SOR base 모델 사용 — 본 파일에서 재정의 금지.
# from app.contracts import DocumentSummary, SourceType  (sdd/contracts.md §5)
```

## 비즈니스 룰

- `/topics/cso/clusters`는 12 클러스터를 캐시 (TTL=24h). 클라이언트는 **온보딩 화면과 설정의 관심 분야 화면 모두에서 본 endpoint를 호출**한다 (단일 진실 공급원).
- 동적 리프 토픽 GET은 사용자별 격리 (`user_id` JWT 클레임 기반 필터).
- `/topics/{topic_id}/documents`는 다음 우선순위로 필터 (**A8 머지 후 적용** — 1차 시연 A4~A6 단계에서는 SELECT DISTINCT + ORDER BY 만, 필터 비활성. router.py:1 주석 참조): 1) 사용자 `NotInterestedTopic` 제외, 2) 사용자 `HiddenDocument` 제외, 3) `ClickbaitResult.decision='clickbait'` 제외 (FR-31). **`SavedDocument` 는 제외 X** — 사용자가 저장해도 토픽 페이지에는 계속 표시 (저장 목록은 별도 `/feedback/saved`). recommendation-ranking.md §Core 의 후보 제외 룰 (SavedDocument 후보 제외) 은 `/recommendations/dashboard` 전용이고 본 endpoint 는 토픽 탐색용이므로 분리.
- `/topics/traces`는 사용자 자신의 trace만 반환 (`user_id` JWT 클레임 필터). active+stale만 default, `?status=archived` 명시 시 archived 포함.
- `score_tail`은 NFR-04 마스킹 대상 — 일반 사용자 응답에서 null 반환. 관리자 endpoint(`/admin/users/{id}/...`)에서만 실제 값.

## 오류 응답

| code | HTTP | 의미 |
|---|---|---|
| `topic.not_found` | 404 | UUID 없음 |
| `topic.unauthorized_leaf` | 403 | 다른 사용자 리프 토픽 접근 |
| `topic.linkage_error` | 503 | 토픽 연결 오류 (FR-64, NFR-08) — `/topics/cso/clusters` 가 12 cluster 보장 실패 시 (CSO 미임포트·시드 부분 누락). 관리자 콘솔에 표시. |
