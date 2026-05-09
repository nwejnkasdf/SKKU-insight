# Rate Limiting

본 파일은 SKKU InSight의 slowapi + Redis 기반 rate limit 정책과 키 디자인을 정의한다. 환경변수는 [`../ops/env-vars.md`](../ops/env-vars.md), 위협 모델 매핑은 [`threat-model.md`](threat-model.md).

## 결정 핀

- 라이브러리: `slowapi` (FastAPI + flask-limiter 포팅)
- 백엔드 store: Redis. `REDIS_URL_RATE_LIMIT` (별도 DB)
- 식별자:
  - 인증 전 엔드포인트 → IP 기반 (`X-Forwarded-For` 신뢰는 신뢰 가능 프록시 뒤에서만)
  - 인증된 엔드포인트 → user_id 또는 admin_id

## 정책 표

| 엔드포인트 | 제한 | 식별자 | 환경변수 | 위반 응답 |
|---|---|---|---|---|
| POST `/auth/signup` | 3/시간 | IP | `RATE_LIMIT_SIGNUP` | 429 + `auth.rate_limited` |
| POST `/auth/login` | 5/분 | IP | `RATE_LIMIT_LOGIN` | 429 |
| POST `/auth/refresh` | 60/시간 | user_id | `RATE_LIMIT_DEFAULT` 보다 관대 | 429 |
| POST `/auth/logout` | 30/분 | user_id | | 429 |
| POST `/admin/auth/login` | 5/분 | IP | `RATE_LIMIT_LOGIN` | 429 |
| POST `/events`, `/events/batch` | 600/분 | user_id | 별도 정책 | 429 |
| 그 외 인증된 GET/POST | 60/분 | user_id | `RATE_LIMIT_DEFAULT` | 429 |
| POST `/collection/jobs/me/run-now` | 1/시간 | user_id | `RATE_LIMIT_RUN_NOW` | 429 |
| POST `/consent/revoke` | 5/시간 | user_id | `RATE_LIMIT_REVOKE_CONSENT` | 429 |
| POST `/consent/account-deletion` | 1/시간 | user_id | `RATE_LIMIT_DELETE_ACCOUNT` | 429 |

## 키 디자인 (Redis)

slowapi 기본은 token bucket. Redis 키:

```
KEY: ratelimit:{namespace}:{identifier}:{window_start}
TYPE: string (counter)
TTL: window 길이

예:
  ratelimit:login:1.2.3.4:202605091230  (1분 window)
  ratelimit:default:user_id:2026050912  (1분 window)
```

slowapi가 자동 관리하지만 운영 가시성을 위해 namespace 명시.

## 응답 헤더

표준 `Retry-After` + 가시성 헤더:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 47
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1717000947
Content-Type: application/json

{"code":"auth.rate_limited","message":"잠시 후 다시 시도해주세요."}
```

## 우회 방지

- `slowapi.util.get_remote_address`는 `X-Forwarded-For` 사용. **신뢰 가능한 reverse proxy 뒤에서만** 안전. 1차 시연은 docker compose 직접 노출이라 IP 위조 위험 낮음. 운영 시는 nginx/ingress 추가 후 신뢰 IP 화이트리스트.
- 인증된 엔드포인트는 user_id 기반이므로 IP 우회 무관. Auth 자체는 IP 기반 — 같은 IP에서 여러 사용자가 가입 시도하면 unfair.

## 추가 보호

- **Captcha** — 1차 시연에서 구현 안 함. 운영 시 회원가입 등에 reCAPTCHA 또는 hCaptcha 도입 권장.
- **계정 잠금** — 본 시스템은 잠금 없이 rate limit만 사용 (시연에서 잠금 풀기 부담). 운영 시 5회 연속 실패 시 짧은 잠금 (5분).

## 테스트

- pytest fixture로 Redis flushdb + 시간 mock
- 5/분 위반 → 6번째 요청 429 검증
- 동일 IP에서 다른 user → 별 카운터인지 검증 (login은 IP 기반이므로 같은 카운터, /events는 user_id 기반이므로 별개)
