# API: Onboarding

본 파일은 신규 사용자 온보딩 API의 엔드포인트 명세이다. UC-01의 동의 → 관심 분야 선택 → cold-start LLM → 첫 대시보드 흐름을 담당한다. 관련 FR: FR-05, FR-07, FR-08, FR-09, FR-10, FR-11, FR-55. 관련 NFR: NFR-18, NFR-19, NFR-26.

연관 문서:
- [`../algorithms/cold-start.md`](../algorithms/cold-start.md) — 입력·LLM 프롬프트·후처리
- [`../algorithms/cso-topic-traversal.md §7`](../algorithms/cso-topic-traversal.md) — cold-start과 trace 시작
- [`auth.md`](auth.md) — signup/login (선행 단계)
- [`consent.md`](consent.md) — UserConsent 등록 (선행 단계)
- [`recommendation.md`](recommendation.md) — 첫 대시보드 조회 (후행 단계)

> **API 통신 규약**: [`../sdd/api-conventions.md`](../sdd/api-conventions.md) 따름. 비동기 응답은 §11 (202 + polling).

## 베이스

- 기본 경로: `/onboarding`
- 인증: 모든 endpoint는 access_token 필수 (`aud="user"`)
- 사전조건: signup 완료 + UserConsent 활성

## 엔드포인트 표

| Method | Path | 설명 | Rate Limit |
|---|---|---|---|
| POST | `/onboarding/interests` | 사용자가 선택한 클러스터 + user_class 제출 + cold-start 트리거 | 5/시간/사용자 |
| GET | `/onboarding/cold-start-status/{request_id}` | cold-start LLM 진행 폴링 | 60/분/사용자 |
| PUT | `/onboarding/interests` | 설정에서 관심 분야 수정 (FR-55) | 10/시간/사용자 |

> **12 CSO 클러스터 조회는 [`topics.md`](topics.md) `GET /topics/cso/clusters` 단일 endpoint** 사용. onboarding 화면도 동일 endpoint를 호출한다 (응답 schema 동일, locale 헤더로 한·영 분기). 별도 `/onboarding/clusters` 두지 않는다.

## 스키마

```python
class ClusterCard(BaseModel):
    broad_interest_id: UUID
    name_ko: str          # "인공지능"
    name_en: str          # "AI"
    description: str      # "Machine learning, NLP, vision, robotics 등"
    cso_seed_topic_id: UUID    # CSO 12 cluster seed topic FK
    display_order: int

class ClustersResponse(BaseModel):
    clusters: list[ClusterCard]   # 12개

class UserClass(str, Enum):
    UNDERGRADUATE = "undergraduate"
    RESEARCHER = "researcher"
    PROFESSOR = "professor"
    GENERAL = "general"

class OnboardingInterestsRequest(BaseModel):
    cso_cluster_ids: list[UUID]              # 사용자가 선택한 cluster (최소 1개)
    user_class: UserClass = UserClass.GENERAL # transient — User 영구 저장 X (decision-backlog.md P1-1)
    locale: Literal["ko", "en"] = "ko"

class OnboardingInterestsResponse(BaseModel):
    request_id: UUID                         # cold-start 작업 추적용
    status: Literal["queued", "completed"]
    polling_url: str                         # GET /onboarding/cold-start-status/{request_id}
    estimated_seconds: int                   # 예상 완료 시간 (8초 기본)

class ColdStartStatusResponse(BaseModel):
    request_id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    progress_percent: int                    # 0~100
    completed_at: datetime | None
    dashboard_ready: bool                    # true면 GET /recommendations/dashboard 가능
    error_code: str | None                   # 실패 시
```

## 비즈니스 룰

### `POST /onboarding/interests`
1. 동의 활성 검증 (FR-11). 비활성이면 403 + `consent.required`
2. `cso_cluster_ids` 길이 0이면 422 + `onboarding.no_cluster_selected`
3. 각 cluster_id가 BroadInterest 테이블에 존재하는지 검증
4. **사용자별 prior boost 적용** (`UserInterestState.alpha_prior` 임시 상승, 14 active days TTL):
   - 선택 cluster의 cso_seed_topic_id에 `alpha_prior + onboarding_prior_boost` 적용
   - `algorithms/interest-bayesian.md` 의 `interest_params.toml` 참고
5. **Cold-start LLM 작업을 RQ에 enqueue** (사용자당 동시 1개, single-flight):
   - 입력: `cso_cluster_ids`, `user_class`(transient, prompt 재료로만), `locale`
   - LLM 호출(model_slot=high) → 10 후보 검증 → Recommendation INSERT
   - sentinel Source(`source_name="cold_start_pseudo"`)를 source_id로 가지는 pseudo Document 행 INSERT 가능 ([`../algorithms/cold-start.md §pseudo-document`](../algorithms/cold-start.md))
6. `User.onboarding_complete = true` 갱신 (즉시)
7. 응답: `request_id`, `polling_url`, `estimated_seconds=8`
8. **단 동기 mode 옵션**(`Prefer: respond=sync` 헤더)이 있으면 8초까지 동기 대기, 초과 시 자동 비동기 전환

### `GET /onboarding/cold-start-status/{request_id}`
- 클라이언트가 1초 간격으로 폴링 (rate limit 60/분/사용자 안에서)
- `status="completed"` + `dashboard_ready=true` 시 클라이언트는 dashboard로 이동
- LLM 실패 시 `status="failed"` + `error_code="cold_start.llm_failed"`. 클라이언트는 trust=high trend fallback으로 채워진 임시 dashboard 표시 ([`../algorithms/cold-start.md §후속 일반 추천과의 transition`](../algorithms/cold-start.md))

### `PUT /onboarding/interests` (FR-55, 설정 화면)
- 동일 schema. 단:
  - **추가 cluster**: 새 cluster를 14 active day prior boost 대상에 추가 (`UserInterestState.alpha_prior` 임시 상승). 기존 trace는 영향 없음.
  - **제거 cluster**: 사용자가 명시적으로 cluster 제거 시 그 cluster를 root로 하는 모든 active trace를 stale 마킹. dynamic leaf는 LLM 검토 후 다른 trace 로 인계 가능 ([`../algorithms/cso-topic-traversal.md §3.5`](../algorithms/cso-topic-traversal.md))
- 응답에는 cold-start 재호출 X (행동이 root이므로 행동 신호로 자연 변동)

## 동시성 가드

`POST /onboarding/interests`는 사용자당 동시 1건 single-flight ([`../sdd/concurrency.md §2`](../sdd/concurrency.md)):

- Redis lock key: `lock:onboarding:{user_id}` TTL 30초
- 중복 요청은 진행 중 작업의 `request_id`를 반환 (idempotent)

## 오류 응답

| code | HTTP | 의미 |
|---|---|---|
| `onboarding.consent_required` | 403 | UserConsent 비활성 |
| `onboarding.no_cluster_selected` | 422 | cso_cluster_ids 빈 배열 |
| `onboarding.invalid_cluster` | 422 | cluster_id가 BroadInterest에 없음 |
| `onboarding.already_in_progress` | 409 | single-flight lock 진행 중 (idempotent — 진행 중 request_id 응답으로 회귀) |
| `onboarding.rate_limited` | 429 | rate limit 초과 |
| `cold_start.llm_failed` | 503 | (status response) LLM 호출 실패 |

## 시드 페르소나에서의 사용

`scripts/seed_personas.py` 가 페르소나별로 다음 시퀀스 자동 호출:

```python
await api.post("/auth/signup", ...)
await api.post("/auth/login", ...)
await api.post("/consent", {"agreed": True, "consent_type": "personalization"})
await api.post("/onboarding/interests", {
    "cso_cluster_ids": persona.cso_cluster_ids,
    "user_class": persona.user_class,
})
# 폴링 또는 sync 모드로 cold-start 완료 대기
```

자세히는 [`../data/seed-personas.md`](../data/seed-personas.md).
