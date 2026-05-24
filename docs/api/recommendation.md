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
| POST | `/recommendations/dashboard/refresh` | 캐시 폐기 후 재계산 (20/분/사용자, 데모 반복 조작 허용) |
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
    saved: bool
    hidden: bool
    not_interested: bool
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
    not_interested: bool
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

### A8-v2 UserProfile cron 내부 ErrorCode (endpoint 부재)

본 두 코드는 daily user_profile cron ([`../../backend/app/worker/jobs/user_profile.py`](../../backend/app/worker/jobs/user_profile.py)) 내부 오류로, 사용자 응답 path 없음. worker 로그 + Prometheus metric + `tests/regressions/test_a9_anti_patterns.py` 회귀 가드에서만 사용. discovery slot 은 본 코드 발생 시 fallback chain (broadening seeds → deepening seeds → 기존 trust=high trend) 으로 자동 진입.

| code | HTTP | 의미 |
|---|---|---|
| `profile.llm_output_invalid` | — | LLM 응답 JSON parse 실패 또는 Pydantic schema 검증 실패. cron 본 사용자 profile 갱신 skip. |
| `profile.bridge_cso_not_found` | — | LLM 응답의 `bridge_cso_topic_id` 가 cso_graph 에 부재. 해당 fusion candidate 만 제거, 다른 candidates 는 유지. 전체 매핑 실패 시 본 코드 + profile.llm_output_invalid 와 함께 skip. **(C-53 라운드, 2026-05-24)** — LLM 의 bridge_cso 결정 자체는 trace↔trace meet-in-the-middle BFS 로 교체 (`apply_fusion_bridge_override`). LLM 응답의 fusion_candidates 는 BFS 결과로 덮어쓰므로 본 ErrorCode 는 BFS 가 None (max_hops 안 만나기) 반환 시에만 발생 — 자연 fallback trend. |

### C-54 fusion bridge 영역 fresh Document fetch (UserProfile cron 안)

**(C-54 라운드, 2026-05-24, [`../decisions.md §17`](../decisions.md))** — `apply_fusion_bridge_override` 가 BFS bridge 결정 직후 LLM web_search 호출 + Document/DocumentTopic INSERT (bridge_cso 단일 매핑). dashboard 다음 조회 시 fusion 카드 자연 채워짐.

흐름:
1. BFS 결정 bridge_cso (cso_graph 멤버 검증됨)
2. 두 trace path 의 최근 saved Document 제목 각 3개 + 직전 30일 fusion fetch URL/title (P1 dedup hint) 을 `trace_json` 에 박음
3. `provider.search_with_tools(trace_json, bridge_label, top_n=FUSION_FETCH_MAX_DOCUMENTS, user_id=...)` 호출
4. SearchResult → `_insert_document_idempotent` + `_upsert_document_topic(LeafTarget(parent=bridge_cso, leaf=None))` — D 사용자 결정: bridge 매핑은 cso_topic 단일

실패 모드 (F1): `Exception` catch → `logger.warning("fusion_fetch failed ... — fusion_candidates preserved")` + 정상 흐름 유지 (fusion_candidates 보존, Document INSERT 0건). dashboard 빈 풀 시 fallback trend.

비용: 사용자당 LLM 1회/일 추가. provider 인터페이스 미확장 (`search_with_tools` 그대로 재사용). 코드: [`../../backend/app/profile/fusion_fetch.py`](../../backend/app/profile/fusion_fetch.py).
