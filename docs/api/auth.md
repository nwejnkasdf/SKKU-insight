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
| POST | `/auth/refresh` | 액세스 토큰 갱신 | refresh_token (cookie 또는 body) | 60/분/사용자 |
| POST | `/auth/logout` | 로그아웃 (refresh 폐기) | access_token | 60/분/사용자 |
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
| `auth.token_expired` | 401 | JWT 만료 (NFR-17) |
| `auth.refresh_revoked` | 401 | refresh 폐기됨 |
| `auth.rate_limited` | 429 | rate limit 초과 |

## 비즈니스 룰

- 신규 가입 시 UserConsent는 별도 `/consent` 호출로 등록한다 (FR-05, FR-11).
- 로그인 성공 시 `last_login_at` 갱신.
- 로그아웃은 Redis의 `refresh:{user_id}:{jti}` 키를 삭제. access_token의 `jti`는 deny-list에 짧게 추가 (15분 TTL).
- 모든 응답은 `X-Request-Id` 헤더 포함 (구조화 로그 상관관계용).

<!-- TODO: A2가 OpenAPI 스펙 자동 생성 시 본 표와 cross-check -->
