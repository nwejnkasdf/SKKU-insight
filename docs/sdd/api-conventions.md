# API 통신 규약 (API Conventions)

본 파일은 SKKU InSight의 모든 HTTP API endpoint가 공통으로 따르는 통신 규약을 정의한다. 각 `docs/api/*.md`의 endpoint 명세는 본 규약을 참조하여 자기 영역 schema만 명시한다. A2/A6/A8/A10 모두 코드 작성 시 본 표준을 따른다.

연관 문서: [`concurrency.md`](concurrency.md) (single-flight·user lock), [`../security/auth-flow.md`](../security/auth-flow.md), [`../security/token-handling.md`](../security/token-handling.md), [`../security/rate-limiting.md`](../security/rate-limiting.md), [`../ops/env-vars.md`](../ops/env-vars.md).

## 1. 기본 형식

| 항목 | 값 |
|---|---|
| Protocol | HTTPS (NFR-20). 개발 시 `localhost` HTTP 허용 |
| Content-Type | `application/json; charset=utf-8` 요청·응답 |
| Charset | UTF-8 강제 |
| 시간 표기 | ISO 8601 with timezone (예: `2026-05-09T12:00:00Z`) |
| 시간대 | 서버는 UTC, 클라이언트는 로컬 → 모든 API는 UTC ISO8601 |
| UUID | 표준 RFC 4122 string (예: `"550e8400-e29b-41d4-a716-446655440000"`) |
| Boolean | JSON `true` / `false` (lowercase, no quotes) |
| Null vs missing | optional 필드는 `null` 명시 또는 키 생략 모두 허용. response는 항상 명시 |

## 2. URL 구조

```
/{area}/[{resource}/[{id}/[{sub}]]]
```

영역(`area`):
- `/auth` — 인증
- `/consent` — 동의
- `/onboarding` — 온보딩
- `/topics` — CSO 토픽 + 동적 리프 (CSO 클러스터 조회는 `GET /topics/cso/clusters` 단일)
- `/interest` — 관심 상태
- `/events` — 행동 로그
- `/feedback` — 명시 피드백 (저장/숨김/관심없음)
- `/recommendations` — 추천 대시보드
- `/documents` — 문서 상세
- `/collection` — 사용자 수집 잡 + 관리자 일부
- `/admin` — 관리자 콘솔 전용 (aud=admin 강제)

API 버저닝: 1차는 prefix 없음. 향후 breaking change 시 `/v2/...` 도입.

## 3. 인증 헤더

```http
Authorization: Bearer <access_token>
```

- 모든 personalization endpoint 필수
- `aud` 클레임이 endpoint와 매칭 (일반: `user`, 관리자: `admin`)
- 토큰 만료 시 `401 + auth.token_expired` → 클라이언트는 `/auth/refresh` 자동 재시도
- 401 응답에는 표준 `WWW-Authenticate: Bearer error="invalid_token"` 헤더 포함
- **401 ErrorCode 분기 룰**: `auth.token_expired` → `/auth/refresh` 시도; `auth.invalid_token` (위조·서명 불일치) 또는 `auth.refresh_revoked` (refresh 폐기됨) → 재로그인 강제 (refresh 시도 X). 클라이언트(A9·A10)는 본 분기로 자동 행동 결정.

## 4. 표준 헤더

### 요청

| 헤더 | 의무 | 의미 |
|---|---|---|
| `Authorization` | personalization | Bearer access_token |
| `Content-Type` | POST/PUT/PATCH | `application/json` |
| `Accept-Language` | 옵션 | `ko` (default) 또는 `en` — 응답 `reason_short` 등 자연어 분기 |
| `X-Request-Id` | 옵션 | 클라이언트 발급 UUID. 서버 로그에 동일 값 기록 |
| `X-Idempotency-Key` | POST `/events`, `/feedback/*`, `/onboarding/interests` | 32자 random — 재시도 시 중복 방지 |
| `Prefer` | 옵션 | `respond=sync` (cold-start 동기 모드 등) |

### 응답

| 헤더 | 의미 |
|---|---|
| `Content-Type` | `application/json; charset=utf-8` |
| `X-Request-Id` | 서버가 발급 또는 echo (요청 헤더 없을 시 발급) |
| `X-Server-Time` | 서버 ISO8601 시각. 클라이언트 시계 drift 감지용 |
| `Retry-After` | 429 응답 시 초 단위 대기 권장값 |
| `WWW-Authenticate` | 401 응답 시 |
| `Cache-Control` | GET 응답: 보통 `private, max-age=0` (캐시 미사용) |

## 5. 표준 응답 — Naked vs Envelope

본 시스템은 **naked response 채택**. 즉:

```json
// GET /topics/cso/{id} 응답
{
  "cso_topic_id": "...",
  "label": "...",
  ...
}
```

`{data: {...}, meta: {...}}` envelope은 사용하지 않음. 다만 list 응답은 §6 페이지네이션 envelope 채택.

## 6. 페이지네이션 (list 응답 표준)

모든 list 응답은 cursor 기반 페이지네이션 envelope.

### 요청 쿼리

```
GET /topics/leaves?status=active&cursor=eyJ...&limit=20
```

| 파라미터 | 의무 | 기본 | 의미 |
|---|---|---|---|
| `cursor` | 옵션 | null (첫 페이지) | 서버 발급 opaque base64 토큰 |
| `limit` | 옵션 | 20 | 페이지 크기. 최대 100 |
| (필터) | 옵션 | — | 영역별 정의 (예: `status=active`) |

### 응답

```python
class PageMeta(BaseModel):
    next_cursor: str | None     # null 이면 마지막 페이지
    has_more: bool
    page_size: int               # 실제 반환된 row 수

class PagedResponse(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta
```

list 응답에서 envelope 형태 (`items + meta`)는 본 패턴만 사용.

## 7. 표준 에러 응답

### Schema

```python
class ErrorResponse(BaseModel):
    code: str            # "auth.invalid_credentials" 형식
    message: str         # 한국어 사용자 메시지
    details: dict | None # 추가 컨텍스트 (validation 실패 필드 등)
    request_id: str      # X-Request-Id 값 echo
```

```json
{
  "code": "auth.invalid_credentials",
  "message": "이메일 또는 비밀번호가 올바르지 않습니다.",
  "details": null,
  "request_id": "..."
}
```

### code 명명 규약

`{area}.{specific}` 점 표기. 예:
- `auth.invalid_credentials`
- `auth.token_expired`
- `event.consent_required`
- `recommendation.cold_start_in_progress`
- `admin.role_insufficient`

### HTTP 상태 코드 매핑

| 상태 | 사용 |
|---|---|
| 200 | 정상 (A6 idempotency match — 기존 row 반환 포함) |
| 201 | 자원 생성 (signup, INSERT 후) |
| 202 | 비동기 처리 시작 (cold-start, reprocess) |
| 204 | 본문 없는 성공 (DELETE) |
| 207 | **Multi-Status** — batch 부분 성공 (A6: `POST /events/batch` 응답. 본문 `{items: [{event_id, accepted, error_code}], total_accepted}`. entry 단위 consent gate / idempotency mismatch 등 부분 실패 허용) |
| 400 | 잘못된 요청 형식 |
| 401 | 인증 실패 (토큰 없음/만료/위조) |
| 403 | 권한 부족 (admin role, 동의 비활성) |
| 404 | 자원 없음 |
| 409 | 충돌 (중복 가입, 이미 진행 중, A6 idempotency mismatch `EVENT_DUPLICATE`) |
| 422 | 의미 검증 실패 (Pydantic validation, A6 `/events/batch` max 50 entries 초과) |
| 429 | rate limit (Retry-After 헤더) |
| 503 | 외부 의존(LLM, clickbait) 일시 실패 |

## 8. Idempotency

POST 중 **상태 변경 + 사용자 명시 의도**인 endpoint는 idempotency 보장:

| Endpoint | 키 |
|---|---|
| `POST /events`, `POST /events/batch`, `POST /feedback/*` | request body의 `client_request_id` (동일 값으로 재호출 시 기존 row 반환) |
| `POST /onboarding/interests` | `X-Idempotency-Key` 헤더 (없으면 user-level single-flight lock으로 대체, [`concurrency.md §2`](concurrency.md)) |
| `POST /admin/collection/jobs/{id}/reprocess` | job_id 자체로 idempotency (이미 큐잉되어 있으면 409) |
| `POST /consent`, `POST /consent/revoke`, `POST /consent/account-deletion` | 자연 idempotent (같은 상태 두 번 → no-op) |

## 9. Rate Limiting 응답

429 응답:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1717000060

{
  "code": "auth.rate_limited",
  "message": "잠시 후 다시 시도해주세요.",
  "details": {"retry_after_seconds": 60},
  "request_id": "..."
}
```

자세한 정책은 [`../security/rate-limiting.md`](../security/rate-limiting.md).

## 10. CORS

`docker compose`의 `CORS_ALLOWED_ORIGINS` 환경변수 ([`../ops/env-vars.md`](../ops/env-vars.md)):

```
CORS_ALLOWED_ORIGINS=http://localhost:3001,app://insight
```

- preflight `OPTIONS` 응답은 표준 헤더 (`Access-Control-Allow-Origin`, `Access-Control-Allow-Headers: Authorization,Content-Type,X-Idempotency-Key,X-Request-Id`, `Access-Control-Allow-Methods`)
- credential 요청 X (모든 인증은 Authorization 헤더)

## 11. 비동기 작업 (cold-start, reprocess)

장시간 작업은 202 + polling URL.

```http
HTTP/1.1 202 Accepted
Location: /onboarding/cold-start-status/{request_id}

{
  "request_id": "...",
  "status": "queued",
  "polling_url": "/onboarding/cold-start-status/{request_id}",
  "estimated_seconds": 8
}
```

폴링 endpoint 응답 status: `queued | running | completed | failed`. 자세한 클라이언트 폴링 룰은 [`../ux/client-behaviors.md §3`](../ux/client-behaviors.md).

## 12. 입력 검증

- Pydantic v2 BaseModel 사용
- 422 응답은 Pydantic 표준 ValidationError를 ErrorResponse로 변환:
  ```json
  {
    "code": "validation_error",
    "message": "입력값을 확인해주세요.",
    "details": {
      "fields": [
        {"loc": ["body", "email"], "msg": "value is not a valid email", "type": "value_error.email"}
      ]
    },
    "request_id": "..."
  }
  ```

## 13. 응답 데이터 마스킹 (NFR-04, FR-32)

- 일반 사용자 응답에서 절대 노출 X:
  - `UserInterestState.long_alpha/long_beta/short_alpha/short_beta/long_score/short_score` (bucket으로만 변환)
  - `Recommendation.score`
  - `ClickbaitResult.confidence/decision`
  - `User.password_hash`
  - `UserEvent.dwell_ms` (다른 사용자에 대해)
- 관리자 응답(`/admin/*`)은 super/operator 권한 시 노출, read_only는 마스킹
- `email`은 운영자 권한에선 부분 마스킹 (`g***d@gmail.com`), super만 전체

## 14. OpenAPI 자동 생성·cross-check + Codegen

### 14.1 OpenAPI export

A2가 FastAPI 기동 후 `python scripts/export_openapi.py > openapi.json` 으로 spec export. CI가 매 PR에서 자동 export → `git diff --exit-code openapi.json` 으로 commit과 일치 강제.

### 14.2 Client codegen

```
backend openapi.json
    ↓ openapi-typescript-codegen
client/src/generated/api.ts          ← A9 Electron client가 import
admin-console/src/generated/api.ts   ← A10 Next.js admin이 import
```

A9·A10은 **endpoint를 raw fetch로 호출 금지**. 모든 호출은 codegen된 typed client만. A2가 시그니처 변경 시 OpenAPI 변경 → codegen 결과 변경 → 빌드 깨짐으로 즉시 발견.

### 14.3 Cross-check 스크립트 (6종, CI 강제)

| 스크립트 | 검증 |
|---|---|
| `scripts/check_api_docs.py` | OpenAPI ↔ `docs/api/*.md` endpoint 표 일치 |
| `scripts/check_schema.py` | SQLAlchemy 모델 ↔ `docs/data/schema.md` 컬럼 일치 |
| `scripts/check_env.py` | `BaseSettings` ↔ `docs/ops/env-vars.md` 변수 일치 |
| `scripts/check_error_codes.py` | `app/contracts.py::ErrorCode` ↔ `docs/api/*.md` 오류 표 일치 |
| `scripts/check_redis_keys.py` | `app/contracts.py::RedisKey` ↔ `docs/sdd/concurrency.md` 키 디자인 일치 |
| `scripts/check_contracts.py` | contracts.py enum ↔ alembic CHECK 제약 일치, raw f-string Redis key 금지 |

GitHub Actions matrix에서 PR마다 실행. 깨지면 merge 차단.

자세한 멀티 에이전트 운영은 [`agent-orchestration.md`](agent-orchestration.md), enum·error code·Redis key SOR 정의는 [`contracts.md`](contracts.md).

## 15. Deprecation 정책

본 1차 시연에서는 deprecation 없음 (모든 endpoint live). 향후 변경 시:

- 응답 헤더 `Deprecation: true` + `Sunset: <date>`
- 변경 6주 전 사전 공지 (없음, 1차 시연 단계)

## 16. 테스트 (A11)

`tests/api/test_conventions.py`:
- 모든 endpoint가 X-Request-Id echo
- 401 응답에 WWW-Authenticate 헤더
- 429 응답에 Retry-After
- 422 응답이 ErrorResponse schema
- list 응답이 PagedResponse envelope
- naked response (envelope 미사용) — admin/list 외
