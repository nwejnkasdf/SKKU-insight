# API: Interest

본 파일은 사용자 관심 상태 조회·갱신, 행동 로그 수집 API 명세이다. 관련 FR: FR-12, FR-17, FR-18, FR-19, FR-20, FR-54. (FR-55 관심 분야 수정은 [`onboarding.md`](onboarding.md) `PUT /onboarding/interests`에서 담당). 관련 NFR: NFR-04 (점수 비노출), NFR-05, NFR-06.

> **Onboarding 단일화 안내**: 이전 버전의 `POST /interest/onboarding` 은 폐기됐다. 신규 가입 시 초기 관심 상태 생성과 cold-start LLM 호출은 [`onboarding.md`](onboarding.md) `POST /onboarding/interests` 에서 통합 담당한다.

> **API 통신 규약**: [`../sdd/api-conventions.md`](../sdd/api-conventions.md) 따름. events idempotency는 `client_request_id` 또는 `X-Idempotency-Key`.

## 베이스

- 기본 경로: `/interest` 및 `/events`
- 인증: 모두 access_token + 동의 활성

## 엔드포인트 표

| Method | Path | 설명 | 비고 |
|---|---|---|---|
| GET | `/interest/state` | 자기 관심 상태 조회 | **NFR-04: 일반 사용자는 점수 노출 X. 응답에서 long_score/short_score는 마스킹** |
| POST | `/events` | 행동 로그 1건 기록 | click/dwell/save/hide/not_interested/...|
| POST | `/events/batch` | 여러 이벤트 한 번에 | dwell tick 등 |
| POST | `/feedback/save` | 저장 (UI 명시 액션) | SavedDocument 생성 + UserEvent + Bayesian 갱신 |
| POST | `/feedback/hide` | 숨김 | HiddenDocument 생성 + Bayesian 갱신 |
| POST | `/feedback/not-interested` | 관심 없음 | NotInterestedTopic 생성 + Bayesian 갱신 |
| GET | `/feedback/saved` | 저장 목록 (UI-05) | |
| GET | `/feedback/hidden` | 숨김 목록 (UI-05) | |
| DELETE | `/feedback/saved/{document_id}` | 저장 해제 | |

## 스키마

```python
EventType = Literal[
    "view",         # 카드 노출 (impression)
    "click",        # 카드 클릭
    "dwell_tick",   # 체류 30초 단위 tick
    "open_external",# 원문 외부 링크 클릭
    "save",
    "hide",
    "not_interested",
]

class EventRequest(BaseModel):
    event_type: EventType
    document_id: UUID | None
    cso_topic_id: UUID | None     # not_interested 시 토픽 단위 가능
    leaf_topic_id: UUID | None
    dwell_ms: int | None
    occurred_at: datetime         # 클라이언트 시계
    client_request_id: str        # idempotency

class EventBatchRequest(BaseModel):
    events: list[EventRequest]    # 최대 50

class EventResponse(BaseModel):
    event_id: UUID
    accepted: bool
    server_received_at: datetime

class InterestStateResponse(BaseModel):
    user_id: UUID
    topics: list[InterestTopicView]
    updated_at: datetime

class InterestTopicView(BaseModel):
    cso_topic_id: UUID | None
    leaf_topic_id: UUID | None
    label: str
    # 일반 사용자 응답에는 noisy bucket만 노출:
    bucket: Literal["high", "medium", "low", "neutral"]  # NFR-04
    # 점수 자체는 노출하지 않음. 관리자 API에서만 long_score/short_score 노출.

class SaveFeedbackRequest(BaseModel):
    document_id: UUID

class HideFeedbackRequest(BaseModel):
    document_id: UUID

class NotInterestedRequest(BaseModel):
    cso_topic_id: UUID | None
    leaf_topic_id: UUID | None
    document_id: UUID | None  # 토픽 추론용 hint
```

## 비즈니스 룰

- **모든 이벤트는 동의가 활성일 때만 수집** (FR-59, NFR-19). 미들웨어가 매 요청마다 `consent_active` 검증 (Redis 60s cache, [`../sdd/concurrency.md §7`](../sdd/concurrency.md)). 비활성이면:
  - `POST /events`, `POST /events/batch`, `POST /feedback/*` → **403 + `event.consent_required`** (행동 로그 INSERT 금지). 클라이언트는 UI-05 변형 화면(재동의/계정삭제)으로 redirect.
  - `GET /interest/state`, `GET /feedback/saved`, `GET /feedback/hidden` → **403 + `event.consent_required`** (개인화 정보 조회 차단)
  - `DELETE /feedback/saved/{document_id}` → 동의 비활성이어도 허용 (사용자가 본인 데이터 정리 가능)
- `client_request_id` 기반 idempotency. 같은 ID로 재호출 시 기존 row 반환.
- Bayesian 갱신은 `event_weights.toml` 정의 가중치 사용 ([`../algorithms/interest-bayesian.md`](../algorithms/interest-bayesian.md)). atomic SQL UPSERT 패턴 ([`../sdd/concurrency.md §4.1`](../sdd/concurrency.md)).
- 모든 이벤트는 user-level Redis lock 안에서 처리 ([`../sdd/concurrency.md §3`](../sdd/concurrency.md)). dwell_tick·click·view는 batch buffer로 묶음 (5초 윈도우, [`../sdd/concurrency.md §6`](../sdd/concurrency.md)). save/hide/not_interested는 즉시.
- `/interest/state` 응답에서 점수 자체는 절대 반환하지 않음 (NFR-04). 클라이언트는 bucket만 사용.
- 관리자 API (`/admin/users/{user_id}/interest-state`)는 별도. 본 파일에 미포함 ([`admin.md`](admin.md) 참조).

## 오류 응답

| code | HTTP | 의미 |
|---|---|---|
| `event.consent_required` | 403 | UserConsent 비활성 (FR-59, NFR-19) — 클라이언트는 UI-05 재동의 화면으로 |
| `event.duplicate` | 409 | client_request_id 중복 |
| `event.invalid_target` | 422 | document_id/topic_id 매칭 실패 |
| `feedback.already_saved` | 409 | 중복 저장 |
