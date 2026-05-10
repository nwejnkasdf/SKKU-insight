# API: Recommendation

본 파일은 추천 대시보드 + 문서 상세 API 명세이다. 관련 FR: FR-26, FR-35~45, FR-49~54. 관련 NFR: NFR-01~06, NFR-12. 알고리즘은 [`../algorithms/recommendation-ranking.md`](../algorithms/recommendation-ranking.md), Cold-start은 [`../algorithms/cold-start.md`](../algorithms/cold-start.md).

> **API 통신 규약**: [`../sdd/api-conventions.md`](../sdd/api-conventions.md) 따름. cold-start은 §11 비동기 폴링.

## 베이스

- 기본 경로: `/recommendations`, `/documents`
- 인증: 모두 access_token + 동의 활성

## 엔드포인트 표

| Method | Path | 설명 |
|---|---|---|
| GET | `/recommendations/dashboard` | UI-02 대시보드 10개 카드 |
| POST | `/recommendations/dashboard/refresh` | 캐시 폐기 후 재계산 (1/분/사용자) |
| GET | `/documents/{document_id}` | UI-04 문서 상세 |
| GET | `/documents/{document_id}/summary` | 섹션형 생성 요약 (FR-51) |

## 스키마

```python
SlotType = Literal["core", "adjacent", "discovery", "fallback_adjacent", "fallback_trend"]

class RecommendationCard(BaseModel):
    recommendation_id: UUID
    document_id: UUID
    slot_type: SlotType
    title: str
    source_name: str
    source_type: SourceType   # contracts.py SOR enum (sdd/contracts.md §2)
    related_topics: list[TopicChip]   # 한국어 라벨
    reason_short: str                 # 한국어, 1문장. NFR-03
    published_at: datetime
    thumbnail_url: str | None
    # 점수 미노출 (NFR-04)
    # source_type 은 contracts.py SourceType enum 사용 (sdd/contracts.md §2)

class TopicChip(BaseModel):
    topic_id: UUID
    label: str
    type: Literal["cso", "leaf"]

class DashboardResponse(BaseModel):
    user_id: UUID
    cards: list[RecommendationCard]   # 항상 10개
    slots: list[SlotSummary]
    generated_at: datetime
    cache: Literal["hit", "miss"]
    cold_start: bool                  # 첫 대시보드면 true (Cold-start LLM 결과)

class SlotSummary(BaseModel):
    slot_type: SlotType
    target_count: int
    actual_count: int
    fallback_reason: str | None       # FR-42, FR-43

class DocumentDetailResponse(BaseModel):
    document_id: UUID
    title: str
    source_name: str
    source_type: SourceType   # contracts.py SOR enum (sdd/contracts.md §2)
    url: str
    canonical_url: str | None
    published_at: datetime
    summary_short: str               # 출처 abstract 또는 LLM 짧은 요약
    related_topics: list[TopicChip]
    saved: bool
    hidden: bool
    # 행동 로그/관심 점수 노출 X (FR-53)

class DocumentSummarySection(BaseModel):
    section: Literal["core", "background", "significance", "limitations"]
    title_ko: str
    body_ko: str

class DocumentSummaryResponse(BaseModel):
    document_id: UUID
    sections: list[DocumentSummarySection]   # FR-51
    generator: Literal["llm", "source_abstract"]
    generated_at: datetime
    reason_short: str                       # FR-52 짧은 토픽 근거 (한국어)
```

## 비즈니스 룰

- `GET /recommendations/dashboard`:
  1. 동의 활성 확인 (FR-59) — Redis 60초 cache로 dump query 회피 ([`../sdd/concurrency.md §7`](../sdd/concurrency.md))
  2. Redis 캐시 `recommendation:{user_id}` hit이면 즉시 반환 (<50ms 목표)
  3. miss이면 **single-flight Redis lock** 획득 후 recommendation-engine 호출. 다른 요청이 build 중이면 0.2초 폴링으로 결과 대기 (최대 8초). 자세한 패턴은 [`../sdd/concurrency.md §2`](../sdd/concurrency.md)
  4. lock 획득 시 build 후 결과를 캐시에 set (TTL 1시간 또는 다음 collection cron 직전), lock 해제
  5. `cold_start=true`인 사용자는 LLM cold-start 경로 ([`../algorithms/cold-start.md`](../algorithms/cold-start.md)) — 폴링 패턴은 single-flight과 동일 시점에서 작동
  6. NFR-12: p95 3초. 캐시 hit 시 <200ms 목표
  7. SavedDocument/HiddenDocument는 응답에 saved/hidden flag로 노출
  8. **캐시 무효화 정책**: save/hide/not_interested/refresh 명시 액션에만 invalidate. 단순 click·dwell은 캐시 유지하고 베이지안 비동기 갱신만 ([`../sdd/concurrency.md §6`](../sdd/concurrency.md))
- `slot_type`이 `fallback_*`이면 `fallback_reason` 필드 필수 (FR-42).
- 전체 후보 부족 시 (FR-43) `slots` 안에 `fallback_trend` slot이 추가되어 `actual_count`가 늘어남. 합계는 항상 10.
- `DocumentSummary`는 LLM 생성 결과를 캐시 (Document당 1회). 실패 시 `generator="source_abstract"` fallback.
- `reason_short`는 한국어 1문장. 토픽 ID나 점수를 직접 포함하지 않고 자연어로 표현 (NFR-04).

## 오류 응답

| code | HTTP | 의미 |
|---|---|---|
| `recommendation.consent_required` | 403 | 동의 철회 상태 (FR-59) |
| `recommendation.cold_start_in_progress` | 202 | LLM 호출 중 (클라이언트는 폴링) |
| `document.not_found` | 404 | |
| `document.summary_unavailable` | 503 | LLM 실패, 출처 abstract 사용 권유 |
