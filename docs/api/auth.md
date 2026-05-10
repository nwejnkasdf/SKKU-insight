# API: Auth

본 파일은 SKKU InSight 인증 API의 엔드포인트 명세이다. 시그니처 수준이며 구현 디테일(예: bcrypt 호출, JWT 클레임 구성)은 [`../security/auth-flow.md`](../security/auth-flow.md), [`../security/token-handling.md`](../security/token-handling.md) 참고. 관련 FR: FR-01, FR-02, FR-04. 관련 NFR: NFR-15, NFR-16, NFR-17, NFR-20.

> **API 통신 규약**: 본 endpoint들은 [`../sdd/api-conventions.md`](../sdd/api-conventions.md)의 표준(JSON 직렬화, ErrorResponse schema, 표준 헤더, idempotency, rate limit 응답)을 따른다. 본 파일은 영역 고유 schema만 명시.

## 베이스

- 기본 경로: `/auth`
- 모든 응답은 JSON
- 보안: TLS 강제 (NFR-20). 비밀번호 정책은 [`../security/password-policy.md`](../security/password-policy.md).

## 엔드포인트 표

| Method | Path | 설명 | 인증 | Rate Limit |
|---|---|---|---|---|
| POST | `/auth/signup` | 회원가입 | none | 3/시간/IP |
| POST | `/auth/login` | 로그인 | none | 5/분/IP |
| POST | `/auth/refresh` | 액세스 토큰 갱신 | refresh_token (request body) | 60/시간/사용자 |
| POST | `/auth/logout` | 로그아웃 (refresh 폐기) | access_token | 30/분/사용자 |
| GET | `/auth/me` | 자기 정보 조회 | access_token | 60/분/사용자 |

## 스키마 (Pydantic 의사 코드)

```python
class SignupRequest(BaseModel):
    email: EmailStr
    password: str  # 12자 이상, password-policy.md 룰 검증

class SignupResponse(BaseModel):
    user_id: UUID
    email: EmailStr
    onboarding_required: bool  # 신규는 항상 true
    consent_required: bool

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenPair(BaseModel):
    access_token: str       # JWT, 15분 만료
    refresh_token: str      # opaque, 14일 만료 (Redis 메타로 검증)
    token_type: Literal["Bearer"]
    expires_in: int         # access 만료 초

class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    # 로그아웃 시 refresh token 도 함께 전달해 폐기 (codex C-2, decision-backlog C-13).
    # body 가 없으면 access 만 denylist 되고 refresh 는 보안상 함께 만료시키지 못함.
    refresh_token: str | None = None

class MeResponse(BaseModel):
    user_id: UUID
    email: EmailStr
    created_at: datetime
    consent_active: bool
    onboarding_complete: bool
```

## 오류 응답

표준 problem detail 형식.

```python
class ErrorResponse(BaseModel):
    code: str            # "auth.invalid_credentials", "auth.email_taken", ...
    message: str         # 한국어 사용자 메시지
    details: dict | None
```

| code | HTTP | 의미 |
|---|---|---|
| `auth.email_taken` | 409 | 이메일 중복 |
| `auth.weak_password` | 422 | 비밀번호 정책 위반 |
| `auth.invalid_credentials` | 401 | 로그인 실패 |
| `auth.token_expired` | 401 | JWT 만료 (NFR-17) — 클라이언트는 `/auth/refresh` 자동 재시도 |
| `auth.invalid_token` | 401 | JWT 위조·형식 오류·서명 불일치 — 재인증 필요 (refresh 불가) |
| `auth.refresh_revoked` | 401 | refresh 폐기됨 — 재인증 필요 |
| `auth.rate_limited` | 429 | rate limit 초과 |

## 비즈니스 룰

- **Email 정규화 3겹**: 모든 endpoint(signup/login/me)의 email 필드는 `email.strip().lower()` 정규화 후 처리·저장. 클라이언트가 `Test@TEST.com` 보내도 백엔드는 `test@test.com`으로 통일. 1) Pydantic validator(요청 경계) + 2) service 계층(방어적) + 3) DB functional index `LOWER(email)` partial UNIQUE — 3겹 ([`../security/auth-flow.md`](../security/auth-flow.md), [`../data/schema.md`](../data/schema.md) User).
- 신규 가입 시 UserConsent는 별도 `/consent` 호출로 등록한다 (FR-05, FR-11).
- 로그인 성공 시 `last_login_at` 갱신.
- 로그아웃: access_token 의 `jti` 를 deny-list 에 추가 (잔여 access TTL) + body 로 받은 `refresh_token` 도 함께 폐기 (HMAC index 값을 `:revoked` 로 OVERWRITE + meta `active='0'`). body 없으면 access 만 폐기되고 refresh 는 보안 결함이므로 클라이언트는 항상 refresh body 전달 권장.
- **Refresh replay 감지**: 회전된 토큰 재사용 시 user의 모든 refresh family revoke (HMAC `:rotated` 마커 패턴, [`../security/token-handling.md`](../security/token-handling.md)).
- 모든 응답은 `X-Request-Id` 헤더 포함 (구조화 로그 상관관계용).

OpenAPI는 `scripts/check_api_docs.py`가 본 표와 자동 cross-check (A2 구현).
