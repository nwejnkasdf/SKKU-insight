# API: Consent

본 파일은 개인정보·개인화 동의 관리 API 명세이다. 관련 FR: FR-05, FR-06, FR-11, FR-56, FR-58, FR-59. 관련 NFR: NFR-18, NFR-19, NFR-21, NFR-26.

> **API 통신 규약**: [`../sdd/api-conventions.md`](../sdd/api-conventions.md) 따름.

## 베이스

- 기본 경로: `/consent`
- 인증: 모든 엔드포인트 access_token 필수

## 엔드포인트 표

| Method | Path | 설명 | 인증 | Rate Limit |
|---|---|---|---|---|
| GET | `/consent` | 자기 동의 상태 조회 | access_token | 60/분/사용자 |
| POST | `/consent` | 동의 등록/갱신 | access_token | 10/분/사용자 |
| POST | `/consent/revoke` | 동의 철회 | access_token | 5/시간/사용자 |
| POST | `/consent/account-deletion` | 계정·개인화 데이터 삭제 요청 | access_token | 1/시간/사용자 |

## 스키마

```python
ConsentType = Literal["personalization"]  # 1차는 단일 타입. EV 시 마케팅 등 추가.

class ConsentRequest(BaseModel):
    consent_type: ConsentType
    agreed: bool                # true만 허용 (false는 /revoke로)

class ConsentRecord(BaseModel):
    consent_id: UUID
    consent_type: ConsentType
    agreed_at: datetime
    revoked_at: datetime | None

class ConsentStateResponse(BaseModel):
    user_id: UUID
    records: list[ConsentRecord]
    active: bool                # personalization 동의가 유효하면 true
    onboarding_required: bool

class ConsentRevokeRequest(BaseModel):
    consent_type: ConsentType
    confirmation: Literal["confirm"]  # 사용자 의도 명시 안전장치

class AccountDeletionRequest(BaseModel):
    reason: str | None
    confirmation: Literal["confirm"]

class AccountDeletionResponse(BaseModel):
    request_id: UUID
    status: Literal["queued"]
    # 1차 시연: now() + 5분 (RQ async worker가 CASCADE DELETE 실행, A2 결정 2026-05-11).
    # 정상 시 30s~2min 내 완료. NFR-21이 명시한 30일 grace period는 post-시연 폴리시
    # (decision-backlog.md C-2). 폴링 endpoint는 1차 미제공 — 5분 후 자동 완료 가정.
    expected_deletion_by: datetime
```

## 비즈니스 룰

- `POST /consent` (agreed=true) 호출 후에만 `/onboarding/interests` 호출 가능 (FR-11). 미동의 사용자가 다른 개인화 API 호출 시 403 + `code=consent.required` (FR-59).
- `POST /consent/revoke` 호출 즉시:
  - UserConsent.revoked_at = now
  - 추천 캐시 폐기
  - 추후 `/recommendations/dashboard` 호출 시 403 + `reauth_required=true` (FR-59)
  - 사용자가 재동의하거나 계정 삭제를 선택할 때까지 클라이언트는 UI-05 변형 화면만 제공
- `POST /consent/account-deletion`:
  - 신규 개인화 처리 즉시 중단 (NFR-21 정합)
  - **1차 시연: RQ async 큐잉 + worker가 5분 내 CASCADE DELETE 실행** (A2 결정 2026-05-11, `decision-backlog.md` C-2). 즉시 inline cascade 대신 async로 변경한 이유: large user data 응답 지연 회피 + 향후 grace period 도입 시 동일 worker 함수 재활용. NFR-21이 명시한 30일 grace period는 post-시연 폴리시.
  - 중복 enqueue 방지: Redis lock `account_deletion:{user_id}` NX EX=600. 중복 시 409 `consent.deletion_in_progress`.
  - 응답 즉시: 사용자 모든 refresh family revoke + recommendation 캐시 폐기 + consent 캐시 invalidate. 실제 row 삭제는 worker.
  - Worker function이 단일 트랜잭션에서 `DELETE FROM "user"` (FK CASCADE 자동 전파) + Redis 정리 (`refresh:{user_id}:*`, `recommendation:{user_id}`, `consent:active:{user_id}`, `lock:*:{user_id}`, `events:buffer:{user_id}` DEL).
  - 삭제 대상 (FK CASCADE로 자동): User, UserConsent, UserEvent, UserInterestState, **UserCSOTraversal**, SavedDocument, HiddenDocument, NotInterestedTopic, DynamicLeafTopic (사용자 소유분), Recommendation 이력.
  - 익명 통계 (ClickbaitResult 집계, CollectionJob 카운트)는 보존.
  - RQ retry max 3 + exponential backoff. 5분 초과 실패 시 admin alert (A11 후속).

## 오류 응답

| code | HTTP | 의미 |
|---|---|---|
| `consent.already_active` | 409 | 이미 동의된 상태 |
| `consent.required` | 403 | 동의 미완료 사용자가 보호 API 호출 |
| `consent.revocation_pending` | 409 | 철회 처리 진행 중 |
| `consent.deletion_in_progress` | 409 | 삭제 잡 이미 큐잉됨 |
