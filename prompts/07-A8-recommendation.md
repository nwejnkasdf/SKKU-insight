# A8 — Recommendation Engine (Phase 2)

> 작업 디렉토리: ``
> **사전조건**: A2 + A3 + A4 + A6 + A7 모두 완료. A8가 모든 의존을 통합하는 마지막 알고리즘 모듈.

## 너의 역할

대시보드 추천 엔진. core/adjacent/discovery 후보 생성 + fallback + Cold-start LLM + diversification + 추천 이유 LLM + cache stampede 방어.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `docs/algorithms/recommendation-ranking.md` (전체)
- `docs/algorithms/cold-start.md` (전체)
- `docs/algorithms/cso-topic-traversal.md` §6 (current/adjacent/proactive 정의)
- `docs/api/recommendation.md`
- `docs/sdd/concurrency.md` §2 single-flight, §5 LLM semaphore
- `docs/data/schema.md` (Recommendation, RecommendationSlot)
- `docs/sdd/data-flow.md` §3 dashboard

## 산출

### 1. `app/recommendation/` 모듈
- `service.py` — `get_dashboard(user_id)` (concurrency.md §2 single-flight 패턴)
- `engine.py` — `build_dashboard(user_id)` 메인 흐름 (recommendation-ranking.md "의사 코드" 그대로)
- `candidates.py` — core/adjacent/discovery 후보 생성 SQL (recommendation-ranking.md "후보 생성" 그대로)
- `ranking.py` — 점수 계산 (`topic_match × w_match + freshness × w_fresh + trust × w_trust`)
- `fallback.py` — FR-42 (slot 부족) + FR-43 (전체 부족) 다단계
- `diversify.py` — source/leaf cap (recommendation-ranking.md "다양성 룰")
- `cold_start.py` — cold-start LLM 호출 + sentinel Source 활용 + pseudo Document INSERT (cold-start.md "후처리" 그대로)
- `reasons.py` — `reason_short` LLM 생성 (model_slot=medium, recommendation-ranking.md "추천 이유")

### 2. 카테고리 정의 (A7 trace 모델 의존)
- current = active trace path 끝 노드 + 산하 active leaf
- adjacent = active trace 끝 노드의 1-hop 그래프 이웃 (path 외)
- proactive = active trace path 외 영역 trust=high 트렌드 + emerging dynamic leaf 후보

### 3. emerging quota
- core 슬롯 5개 중 1개는 emerging leaf 우선 (`recommendation.toml.core_slot_emerging_quota = 1`)
- emerging 후보 부재 시 active leaf로 회수

### 4. Single-flight Redis lock
- `RedisKey.recommendation_build_lock(user_id)` TTL 30초
- 다른 요청은 0.2초 폴링으로 캐시 결과 대기 (최대 8초)
- 8초 초과 시 직접 build (concurrency.md §2)

### 5. Cache 정책
- `RedisKey.recommendation_cache(user_id)` TTL 1시간
- save/hide/not_interested 시 즉시 invalidate (concurrency.md §6)
- click·dwell은 캐시 유지

### 6. Cold-start orchestrator
- `POST /onboarding/interests` 에서 enqueue된 작업 수행 (A2 본문이 RQ enqueue까지만 했음)
- LLM 호출 (model_slot=high) → 10 후보 검증 → URL 매칭 → pseudo Document INSERT (sentinel Source 사용)
- 첫 카드 클릭 시점에 trace 1개 생성은 A7과 협업
- 8초 SLA, 초과 시 polling. cold_start_max_per_day = 100 (interest_params.toml)

### 7. Endpoint 본문
- `GET /recommendations/dashboard` — single-flight + cache + build
- `POST /recommendations/dashboard/refresh` — 1/분/사용자, 캐시 폐기 + 재계산
- `GET /documents/{id}` — Document 조회 + saved/hidden flag
- `GET /documents/{id}/summary` — LLM 섹션형 요약 (model_slot=medium, FR-51), Document당 1회 캐시

### 8. config
- `recommendation.toml` (recommendation-ranking.md "신뢰도 임계" 그대로)

## 헌법 (재강조)

- **카테고리 ↔ 슬롯 1:1 (current/adjacent/proactive ↔ core/adjacent/discovery)**.
- **score 응답 마스킹** (NFR-04). reason_short만.
- **NFR-12 p95 3초** (캐시 hit 기준). cold-start은 8초 SLA 예외 (202 + polling).
- **FR-42 fallback**: 슬롯 부족 시 다른 슬롯 후보로 대체, 저신뢰 강제 X.
- **FR-43 fallback**: 전체 < 10이면 인접 hops=2 → 신뢰 소스 트렌드 → 30일 archive 다단계.
- **emerging quota 1**: core 5 중 1은 emerging 우선 (C-4 해소).
- **Cold-start LLM은 환상 검증 필수** (cold-start.md `validate_cold_start`). url_hint validator + 10개 + 슬롯 분배 검증.
- **pseudo Document는 sentinel Source FK 사용**. content_type="pseudo_cold_start", 24시간 TTL, 원본 매칭 시 merge.

## 검증

```bash
docker compose up -d
# 시드 페르소나 (A12) 또는 fixture 사용
LLM_PROVIDER=mock curl http://localhost:8000/recommendations/dashboard -H "Authorization: Bearer $TOKEN"
# {cards: [10개], slots: [{slot_type, target_count, actual_count, fallback_reason}], cold_start: false, cache: "miss"}

# 동시 20명 single-flight 검증
ab -n 20 -c 20 -H "Authorization: Bearer $TOKEN" http://localhost:8000/recommendations/dashboard
# 한 번 build, 19번 cache hit

# Cold-start
curl -X POST http://localhost:8000/onboarding/interests -d '{"cso_cluster_ids":[...],"user_class":"undergraduate"}' -H "Authorization: Bearer $TOKEN"
# 202 + polling_url
sleep 8
curl http://localhost:8000/onboarding/cold-start-status/{request_id}
# status=completed, dashboard_ready=true
curl http://localhost:8000/recommendations/dashboard
# 10 cold-start cards (slot 5/3/2)

mypy --strict backend/app/recommendation
ruff check
pytest backend/tests/recommendation -v
```

테스트:
- core/adjacent/discovery 후보 SQL 정확성
- fallback FR-42·43 시뮬레이션 (슬롯 부족·전체 부족)
- emerging quota 1 검증
- single-flight 동시 20명 (concurrency)
- cold-start LLM mock fixture → 10 카드 + slot 분배
- pseudo Document INSERT (sentinel Source)
- reason_short 한국어 길이 ≤ 80자, 점수 키워드 미포함

## 출력 형식

기본 + 추가:
- 시드 페르소나 5명에 대한 dashboard 호출 결과 (cache miss/hit 분포)
- cold-start LLM mock fixture 활용 비율
- single-flight 20명 동시 부하 결과 (p95 latency)
- 다음 Phase A9가 봐야 할 사항 (UI에 노출되는 RecommendationCard 정확 schema)
