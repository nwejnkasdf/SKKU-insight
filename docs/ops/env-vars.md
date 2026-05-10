# 환경변수 카탈로그

본 파일은 SKKU InSight의 모든 환경변수를 카탈로그한다. `.env.example`은 본 표를 그대로 옮긴 형태로 유지한다. 비밀값은 `.env`에서만 채우고 git ignore. 관련: [`docker-compose.md`](docker-compose.md), [`admin-bootstrap.md`](admin-bootstrap.md), [`../security/token-handling.md`](../security/token-handling.md), [`../security/rate-limiting.md`](../security/rate-limiting.md).

## Postgres

| Var | 예시 값 | 비고 |
|---|---|---|
| `POSTGRES_DB` | `insight` | 데이터베이스 이름 |
| `POSTGRES_USER` | `insight` | DB 유저 |
| `POSTGRES_PASSWORD` | (비밀) | docker compose host에서만 읽음 |
| `DATABASE_URL` | `postgresql+asyncpg://insight:${POSTGRES_PASSWORD}@postgres:5432/insight` | api/worker가 사용 |
| `PG_API_POOL_MIN` | `5` | api 컨테이너 asyncpg 풀 최소 ([`../sdd/concurrency.md §1`](../sdd/concurrency.md)) |
| `PG_API_POOL_MAX` | `30` | api 풀 최대 — 사용자 20명 + 폴링 + 캐시 갱신 여유 |
| `PG_WORKER_POOL_MIN` | `2` | worker 풀 최소 |
| `PG_WORKER_POOL_MAX` | `10` | worker 풀 최대 — 수집·lifecycle·병합 잡 동시 |

## Redis

| Var | 예시 값 | 비고 |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | 메인 |
| `REDIS_URL_RATE_LIMIT` | `redis://redis:6379/1` | slowapi 카운터 격리 |
| `REDIS_URL_QUEUE` | `redis://redis:6379/2` | RQ 큐 |
| `REDIS_URL_CACHE` | `redis://redis:6379/3` | 추천 캐시 |

## 보안

| Var | 예시 값 | 비고 |
|---|---|---|
| `JWT_SECRET` | (랜덤 64+ char) | HS256 서명. 부팅 시 검증 |
| `JWT_ACCESS_MINUTES` | `15` | NFR-17 |
| `JWT_REFRESH_DAYS` | `14` | |
| `JWT_ISSUER` | `skku-insight` | aud="user" 또는 "admin" |
| `BCRYPT_COST` | `12` | passlib bcrypt log_rounds |

## LLM

| Var | 예시 값 | 비고 |
|---|---|---|
| `LLM_PROVIDER` | **`mock` (default)** / `openai` / `anthropic` / `openrouter` / `codex_oauth` (local experimental) | 1차 부트는 mock 으로 별도 키 없이 동작. 시연·운영은 정식 API 권장. CodexOAuth는 비공식 OAuth 세션이므로 본인 토이 빌드 전용 |
| `LLM_MODEL_HIGH` | provider별. mock: `mock-high`. openai: `gpt-4o` / 사용 가능한 최신 추론 모델. codex_oauth: `gpt-5.5-xhigh` (예시) | 동적 리프 생성·병합 |
| `LLM_MODEL_MEDIUM` | provider별. mock: `mock-medium`. openai: `gpt-4o-mini`. codex_oauth: `gpt-5.5-medium` (예시) | 요약·추천 이유 |
| `CODEX_OAUTH_TOKEN` | (선택; Codex CLI session token) | `LLM_PROVIDER=codex_oauth` 일 때만 필요. local experimental 경로. |
| `OPENAI_API_KEY` | `sk-...` | LLM_PROVIDER=openai 일 때 필수 |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | LLM_PROVIDER=anthropic 일 때 필수 |
| `OPENROUTER_API_KEY` | `sk-or-...` | LLM_PROVIDER=openrouter 일 때 필수 |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `60` | |
| `LLM_DAILY_TOKEN_BUDGET` | `1000000` | 운영 가드, 초과 시 fallback |
| `LLM_MAX_CONCURRENT` | `8` | 전역 동시 LLM 호출 cap — 외부 API rate limit 보호 ([`../sdd/concurrency.md §5`](../sdd/concurrency.md)) |
| `LLM_MAX_CONCURRENT_PER_USER` | `2` | 한 사용자가 burst로 잡을 수 있는 LLM 호출 cap |

## 클릭베이트 모듈

| Var | 예시 값 | 비고 |
|---|---|---|
| `CLICKBAIT_SERVICE_URL` | (운영 결정) | 백엔드가 호출하는 URL. 호스팅·transport와 무관하게 동일 계약 충족 시 swap 가능 ([`../algorithms/clickbait-integration.md`](../algorithms/clickbait-integration.md) §호스팅·transport 추상화) |
| `CLICKBAIT_MODEL_NAME` | `ax-4.0-light-dora-clickbait-v1` | 응답에 그대로 사용 |

## 관리자 부트스트랩

| Var | 예시 값 | 비고 |
|---|---|---|
| `ADMIN_BOOTSTRAP_EMAIL` | `admin@insight.test` | 첫 관리자 이메일 |
| `ADMIN_BOOTSTRAP_PASSWORD` | (강력 비밀번호) | 첫 로그인 시 강제 변경 |
| `ADMIN_BOOTSTRAP_ROLE` | `super` | super | operator | read_only |

## Rate limit

| Var | 예시 값 | 비고 |
|---|---|---|
| `RATE_LIMIT_LOGIN` | `5/minute` | per IP |
| `RATE_LIMIT_SIGNUP` | `3/hour` | per IP |
| `RATE_LIMIT_DEFAULT` | `60/minute` | per user |
| `RATE_LIMIT_RUN_NOW` | `1/hour` | run-now 강제 트리거 |
| `RATE_LIMIT_REVOKE_CONSENT` | `5/hour` | per user |
| `RATE_LIMIT_DELETE_ACCOUNT` | `1/hour` | per user |

## 수집 스케줄

| Var | 예시 값 | 비고 |
|---|---|---|
| `COLLECTION_CRON` | `0 3 * * *` | UTC 기준 매일 3시 (KST 12:00) |
| `COLLECTION_CRON_DEMO` | `0 * * * *` | demo 모드: 매시 |
| `COLLECTION_PER_USER_PARALLEL` | `4` | 사용자별 어댑터 병렬 수 |
| `COLLECTION_GLOBAL_CONCURRENCY` | `8` | 전체 동시 잡 cap |
| `COLLECTION_USER_JITTER_SECONDS` | `300` | 사용자별 잡 시작 시각 분산 윈도우 — 외부 RL 보호 ([`../sdd/concurrency.md §8`](../sdd/concurrency.md)) |
| `LIFECYCLE_EVALUATOR` | `hybrid_d` | hybrid_d | batch_llm | rule_only |
| `MERGE_EVALUATION_CRON` | `0 3 * * 1` | 매주 월 03:00 UTC |
| `INTEREST_DECAY_CRON` | `0 0 * * *` | 매일 자정 UTC (단 active day 차이 없으면 no-op) |

## 동시성 가드

| Var | 예시 값 | 비고 |
|---|---|---|
| `EVENT_BATCH_SIZE` | `20` | dwell_tick/click 이벤트 batch flush 크기 ([`../sdd/concurrency.md §6`](../sdd/concurrency.md)) |
| `EVENT_BATCH_FLUSH_SECONDS` | `5` | batch flush 주기 |
| `RECOMMENDATION_CACHE_TTL_SECONDS` | `3600` | 추천 캐시 TTL (1시간 또는 다음 collection cron 직전 중 짧은 쪽) |
| `RECOMMENDATION_BUILD_LOCK_TTL_SECONDS` | `30` | single-flight build lock TTL |
| `TRAVERSAL_USER_LOCK_TTL_SECONDS` | `10` | trace mutation user-level mutex TTL |
| `CONSENT_CACHE_TTL_SECONDS` | `60` | consent active 상태 Redis 캐시 |

## 외부 소스 키 (있을 때만 채움)

| Var | 예시 값 | 비고 |
|---|---|---|
| `OPENALEX_POLITE_EMAIL` | `dev@insight.test` | OpenAlex politeness |
| `SEMANTIC_SCHOLAR_API_KEY` | (선택) | 없으면 anonymous |

## CORS / 호스트

| Var | 예시 값 | 비고 |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3001,app://insight` | admin-console + electron app |
| `API_PUBLIC_BASE` | `http://localhost:8000` | 클라이언트가 호출 |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |
| `STRUCTLOG_RENDER` | `json` | json | console |

## .env.example 골격

```env
# === Postgres ===
POSTGRES_DB=insight
POSTGRES_USER=insight
POSTGRES_PASSWORD=changeme-strong-password
DATABASE_URL=postgresql+asyncpg://insight:changeme-strong-password@postgres:5432/insight
PG_API_POOL_MIN=5
PG_API_POOL_MAX=30
PG_WORKER_POOL_MIN=2
PG_WORKER_POOL_MAX=10

# === Redis ===
REDIS_URL=redis://redis:6379/0
REDIS_URL_RATE_LIMIT=redis://redis:6379/1
REDIS_URL_QUEUE=redis://redis:6379/2
REDIS_URL_CACHE=redis://redis:6379/3

# === Auth ===
JWT_SECRET=change-this-to-64-char-random-secret-please-do-not-leave-default-here
JWT_ACCESS_MINUTES=15
JWT_REFRESH_DAYS=14
JWT_ISSUER=skku-insight
BCRYPT_COST=12

# === LLM ===
# 1차 부트는 mock(deterministic fixture)으로 키 없이 동작. 실제 LLM 시연은 openai 권장.
LLM_PROVIDER=mock
LLM_MODEL_HIGH=mock-high
LLM_MODEL_MEDIUM=mock-medium
# 정식 API로 토글하려면 위 LLM_PROVIDER=openai 후 아래 키 채움
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
OPENROUTER_API_KEY=
# CodexOAuth는 local experimental — 본인 토이 빌드 전용
CODEX_OAUTH_TOKEN=
LLM_REQUEST_TIMEOUT_SECONDS=60
LLM_DAILY_TOKEN_BUDGET=1000000
LLM_MAX_CONCURRENT=8
LLM_MAX_CONCURRENT_PER_USER=2

# === Clickbait (URL은 운영 시점 결정 — 호스팅·transport와 무관) ===
CLICKBAIT_SERVICE_URL=
CLICKBAIT_MODEL_NAME=ax-4.0-light-dora-clickbait-v1

# === Admin bootstrap ===
ADMIN_BOOTSTRAP_EMAIL=admin@insight.test
ADMIN_BOOTSTRAP_PASSWORD=Admin-Bootstrap-2026!
ADMIN_BOOTSTRAP_ROLE=super

# === Rate limit ===
RATE_LIMIT_LOGIN=5/minute
RATE_LIMIT_SIGNUP=3/hour
RATE_LIMIT_DEFAULT=60/minute
RATE_LIMIT_RUN_NOW=1/hour
RATE_LIMIT_REVOKE_CONSENT=5/hour
RATE_LIMIT_DELETE_ACCOUNT=1/hour

# === Schedule ===
COLLECTION_CRON=0 3 * * *
COLLECTION_PER_USER_PARALLEL=4
COLLECTION_GLOBAL_CONCURRENCY=8
COLLECTION_USER_JITTER_SECONDS=300
LIFECYCLE_EVALUATOR=hybrid_d
MERGE_EVALUATION_CRON=0 3 * * 1
INTEREST_DECAY_CRON=0 0 * * *

# === Concurrency guards ===
EVENT_BATCH_SIZE=20
EVENT_BATCH_FLUSH_SECONDS=5
RECOMMENDATION_CACHE_TTL_SECONDS=3600
RECOMMENDATION_BUILD_LOCK_TTL_SECONDS=30
TRAVERSAL_USER_LOCK_TTL_SECONDS=10
CONSENT_CACHE_TTL_SECONDS=60

# === External ===
OPENALEX_POLITE_EMAIL=dev@insight.test
SEMANTIC_SCHOLAR_API_KEY=

# === CORS / hosts ===
CORS_ALLOWED_ORIGINS=http://localhost:3001,app://insight
API_PUBLIC_BASE=http://localhost:8000
LOG_LEVEL=INFO
STRUCTLOG_RENDER=json
```

## 검증

부팅 시 `pydantic_settings.BaseSettings`로 모든 변수 검증. 누락 또는 타입 오류 시 즉시 실패. 비밀값은 로그에 마스킹.
