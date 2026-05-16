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
| `LLM_PROVIDER` | **`mock` (default)** / `openai`. (v13 round 2 lifespan 가드 — `anthropic`/`openrouter`/`codex_oauth` 는 search_with_tools NotImplementedError 라 boot 거부.) 1차 부트는 mock 으로 별도 키 없이 동작. 시연·운영은 openai 정식 API. |
| `LLM_MODEL_HIGH` | **`gpt-5.5` (default — v13 round 2 사용자 결정)**. mock 일 때도 fixture lookup 이라 model name 무관. 운영자가 OpenAI 의 다른 모델로 토글 가능. | 동적 리프 생성·병합 + search_with_tools |
| `LLM_MODEL_MEDIUM` | **`gpt-5.5` (default — v13 round 2 사용자 결정)**. high/medium 슬롯 모두 동일 모델 (사용자 결정). 토글로 다른 모델 분리 가능. | 요약·추천 이유 |
| `CODEX_OAUTH_TOKEN` | (선택; Codex CLI session token) | `LLM_PROVIDER=codex_oauth` 일 때만 필요. local experimental 경로. |
| `OPENAI_API_KEY` | `sk-...` | LLM_PROVIDER=openai 일 때 필수 |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | LLM_PROVIDER=anthropic 일 때 필수 |
| `OPENROUTER_API_KEY` | `sk-or-...` | LLM_PROVIDER=openrouter 일 때 필수 |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `180` | |
| `LLM_DAILY_TOKEN_BUDGET` | `1000000` | 운영 가드, 초과 시 fallback |
| `LLM_MAX_CONCURRENT` | `8` | 전역 동시 LLM 호출 cap — Redis 분산 semaphore (multi-worker 안전, [`../sdd/concurrency.md §5`](../sdd/concurrency.md), decision-backlog C-19) |
| `LLM_MAX_CONCURRENT_PER_USER` | `2` | 한 사용자가 burst로 잡을 수 있는 LLM 호출 cap (분산) |
| `LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS` | `30` | 분산 semaphore acquire 재시도 한도. 초과 시 `LLMBudgetExceeded` (fallback 진입) |

## 클릭베이트 모듈

| Var | 예시 값 | 비고 |
|---|---|---|
| `CLICKBAIT_SERVICE_URL` | (운영 결정) | 백엔드가 호출하는 URL. 호스팅·transport와 무관하게 동일 계약 충족 시 swap 가능 ([`../algorithms/clickbait-integration.md`](../algorithms/clickbait-integration.md) §호스팅·transport 추상화) |
| `CLICKBAIT_MODEL_NAME` | `ax-4.0-light-dora-clickbait-v1` | 응답에 그대로 사용 |
| `CLICKBAIT_ENABLED` | **`false` (default — v13 라운드 2026-05-11)** | A4 collection orchestrator 가 post-filter 로 clickbait_module 호출할지 여부. 1차 시연 false (LLM 검색이 1차 정제). 사용자가 News 소스 명시 활성화 시 true. |

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
| `RATE_LIMIT_ONBOARDING` | `5/hour` | per user, POST `/onboarding/interests` |
| `RATE_LIMIT_ONBOARDING_UPDATE` | `10/hour` | per user, PUT `/onboarding/interests` |
| `RATE_LIMIT_EVENTS` | `600/minute` | per user, POST `/events`·`/events/batch` |

## 수집 스케줄

| Var | 예시 값 | 비고 |
|---|---|---|
| `COLLECTION_CRON` | `0 3 * * *` | UTC 기준 매일 3시 (KST 12:00) |
| `COLLECTION_CRON_DEMO` | `0 * * * *` | demo 모드: 매시 |
| `COLLECTION_PER_USER_PARALLEL` | `4` | **(v13 라운드 의미 변경)** 사용자 trace 당 동시 LLM 검색 호출 수 (이전: 어댑터 병렬) |
| `COLLECTION_GLOBAL_CONCURRENCY` | `8` | 전체 동시 잡 cap |
| `COLLECTION_USER_JITTER_SECONDS` | `300` | 사용자별 잡 시작 시각 분산 윈도우 — LLM provider RL 보호 ([`../sdd/concurrency.md §8`](../sdd/concurrency.md)) |
| `LIFECYCLE_EVALUATOR` | `hybrid_d` | hybrid_d | batch_llm | rule_only |
| `MERGE_EVALUATION_CRON` | `0 3 * * 1` | 매주 월 03:00 UTC |
| `INTEREST_DECAY_CRON` | `0 18 * * *` | **(A6, 2026-05-17)** 매일 18:00 UTC = 03:00 KST. A6 daily decay cron (lazy 미사용 — 결정 매트릭스). 베이지안 사후 감쇠 + 14-day onboarding boost 만료 일괄 처리. 사용자 active day 차이 없으면 row no-op. |
| ~~`NAVER_CLEANUP_CRON`~~ | ~~`0 17 * * *`~~ | **(v13 라운드 폐기, 2026-05-11)** decision-backlog P1-6 무효. NaverBS4 어댑터 폐기로 tech_news Document 진입 X → cleanup 불필요. .env 에 남아있어도 무시 (A4 scheduler 등록 제거). |

## 동시성 가드

| Var | 예시 값 | 비고 |
|---|---|---|
| `EVENT_BATCH_SIZE` | `20` | dwell_tick/click 이벤트 batch flush 크기 ([`../sdd/concurrency.md §6`](../sdd/concurrency.md)) |
| `EVENT_BATCH_FLUSH_SECONDS` | `5` | batch flush 주기 |
| `RECOMMENDATION_CACHE_TTL_SECONDS` | `3600` | 추천 캐시 TTL (1시간 또는 다음 collection cron 직전 중 짧은 쪽) |
| `RECOMMENDATION_BUILD_LOCK_TTL_SECONDS` | `30` | single-flight build lock TTL |
| `TRAVERSAL_USER_LOCK_TTL_SECONDS` | `10` | trace mutation user-level mutex TTL |
| `CONSENT_CACHE_TTL_SECONDS` | `60` | consent active 상태 Redis 캐시 |

## A6 Interest Bayesian (2026-05-17 추가)

본 7개 env 는 [`../algorithms/interest-bayesian.md`](../algorithms/interest-bayesian.md) 의 동작 파라미터 — TOML config 가 아닌 env 로 노출되는 운영 토글만 본 표. 베이지안 파라미터 자체 (alpha_prior, half_life 등) 는 system_config 테이블 의 `interest_params` JSONB 행에서 관리 (A10 admin-console 가 UI 제공).

| Var | 예시 값 | 비고 |
|---|---|---|
| `INTEREST_PROPAGATION_ENABLED` | `false` | A7 (leaf-lifecycle + traversal) 도입 후 `true`. false 일 때 ingest_event 는 직접 토픽 + 부모 cso_topic_id 만 갱신, trace path 조상 propagation skip. |
| `INTEREST_BOOST_EXPIRY_ACTIVE_DAYS` | `14` | onboarding boost (cluster +1.0 / 1-hop child +0.5) 가 자연 만료되는 active day. decay cron 이 `user.active_day_counter - boost_applied_at_active_day >= N` row 의 alpha 에서 boost 분 차감 + 컬럼 NULL 화. |
| `INTEREST_BATCH_FLUSH_USER_LOCK_TTL_SECONDS` | `10` | EventBuffer flush 시 per-user mutex TTL (초). flush 후 즉시 release. traversal_lock 과 별도. |
| `DWELL_TICK_CAP_TTL_SECONDS` | `3600` | dwell_tick Redis 카운터 TTL (초). 문서당 cap 4회 도달 후 1시간 자연 소멸. |
| `DWELL_TICK_CAP_PER_DOCUMENT` | `4` | dwell_tick 문서당 베이지안 갱신 cap (30s×4=2분). cap 초과 시 베이지안 skip, active_day 와 UserEvent INSERT 는 그대로. SRS 체류 ≥2분 기준 정렬. |
| `EVENT_DUPLICATE_CACHE_TTL_SECONDS` | `86400` | event idempotency payload-hash Redis 캐시 TTL (초). DB UNIQUE(user_id, client_request_id) 가 1차 SOR — 본 캐시는 응답 RTT 단축용. |
| `SYSTEM_CONFIG_REQUIRED` | `true` | **(Codex S-05 fix)** lifespan startup 시 system_config seed (interest_params, event_weights) 누락 동작. `true` (default 운영) → RuntimeError 로 startup 차단 (fail-fast). `false` (테스트 / 의도적 비활성) → WARN + endpoint fallback. |

## 외부 소스 키 (있을 때만 채움)

> **v13 라운드 박스 (2026-05-11)**: A4 Topic-driven Pivot ([`../decisions.md §10`](../decisions.md))으로 6 source 어댑터 폐기. `OPENALEX_POLITE_EMAIL` / `SEMANTIC_SCHOLAR_API_KEY` 는 본 라운드 시점 **dead code** (어댑터 없음). config 에서 즉시 제거하지는 않고 — 향후 어댑터 도입 시 재사용 가능성 위해 보존. **활성 외부 키**: LLM provider 별 API key (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` — §LLM 섹션 참조) + `CSO_DOWNLOAD_URL`.

| Var | 예시 값 | 비고 |
|---|---|---|
| ~~`OPENALEX_POLITE_EMAIL`~~ | ~~`dev@insight.test`~~ | **(v13 라운드 dead)** OpenAlex 어댑터 미구현. 본 env 미사용. |
| ~~`SEMANTIC_SCHOLAR_API_KEY`~~ | ~~(선택)~~ | **(v13 라운드 dead)** Semantic Scholar 어댑터 미구현. 본 env 미사용. |
| `CSO_DOWNLOAD_URL` | `https://cso.kmi.open.ac.uk/downloads/CSO.3.4.csv` | A3 (cso-topic engine). `scripts/import_cso.py` 다운로드 URL. decision-backlog P1-5 — 버전 갱신(3.5+) 시 본 env 만 교체 + `make import-cso --reset --refresh`. |

## CORS / 호스트

| Var | 예시 값 | 비고 |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3001,app://insight` | admin-console + electron app |
| `API_PUBLIC_BASE` | `http://localhost:8000` | 클라이언트가 호출 |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |
| `STRUCTLOG_RENDER` | `json` | json | console |

## Worker 병렬 정책 (decision-backlog C-20)

| Var | 예시 값 | 비고 |
|---|---|---|
| `UVICORN_WORKERS` | `1` | uvicorn worker process 수. 데모 default 1. multi-worker 시 LLM 동시성은 Redis 분산 (자동), DB pool 합산은 운영자 책임 |

**DB connection 합산 공식** (PostgreSQL `max_connections` 초과 방지):

```
total_db_conn = UVICORN_WORKERS * PG_API_POOL_MAX + PG_WORKER_POOL_MAX
              + (admin-console, alembic 등 ad-hoc 클라이언트, ~5)
```

PostgreSQL default `max_connections=100` 기준:

| 워커 | api pool | worker pool | total | 적합 여부 |
|---|---|---|---|---|
| 1 | 30 | 10 | ~45 | ✅ default 데모 |
| 2 | 30 | 10 | ~75 | ✅ |
| 4 | 30 | 10 | ~135 | ❌ — pool 축소 또는 max_connections 200+ |
| 4 | 15 | 10 | ~75 | ✅ — `PG_API_POOL_MAX=15` 로 축소 |

**LLM 동시성은 워커 수 무관** — Redis 분산 semaphore 가 전역 캡 보장.

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
LLM_MODEL_HIGH=gpt-5.5
LLM_MODEL_MEDIUM=gpt-5.5
# 정식 API로 토글하려면 위 LLM_PROVIDER=openai 후 아래 키 채움
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
OPENROUTER_API_KEY=
# CodexOAuth는 local experimental — 본인 토이 빌드 전용
CODEX_OAUTH_TOKEN=
LLM_REQUEST_TIMEOUT_SECONDS=180
LLM_DAILY_TOKEN_BUDGET=1000000
LLM_MAX_CONCURRENT=8
LLM_MAX_CONCURRENT_PER_USER=2
LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS=30

# === Clickbait (URL은 운영 시점 결정 — 호스팅·transport와 무관) ===
CLICKBAIT_SERVICE_URL=
CLICKBAIT_MODEL_NAME=ax-4.0-light-dora-clickbait-v1
# (v13 라운드 2026-05-11) A4 default 비활성. true 로 변경 시 A4 orchestrator post-filter 호출.
CLICKBAIT_ENABLED=false

# === Admin bootstrap ===
ADMIN_BOOTSTRAP_EMAIL=admin@insight.test
ADMIN_BOOTSTRAP_PASSWORD=Bootstrap-Initial-2026-Strong!
ADMIN_BOOTSTRAP_ROLE=super

# === Rate limit ===
RATE_LIMIT_LOGIN=5/minute
RATE_LIMIT_SIGNUP=3/hour
RATE_LIMIT_DEFAULT=60/minute
RATE_LIMIT_RUN_NOW=1/hour
RATE_LIMIT_REVOKE_CONSENT=5/hour
RATE_LIMIT_DELETE_ACCOUNT=1/hour
RATE_LIMIT_ONBOARDING=5/hour
RATE_LIMIT_ONBOARDING_UPDATE=10/hour
RATE_LIMIT_EVENTS=600/minute

# === Schedule ===
COLLECTION_CRON=0 3 * * *
COLLECTION_CRON_DEMO=0 * * * *
COLLECTION_PER_USER_PARALLEL=4
COLLECTION_GLOBAL_CONCURRENCY=8
COLLECTION_USER_JITTER_SECONDS=300
LIFECYCLE_EVALUATOR=hybrid_d
MERGE_EVALUATION_CRON=0 3 * * 1
INTEREST_DECAY_CRON=0 18 * * *
NAVER_CLEANUP_CRON=0 17 * * *

# === Concurrency guards ===
EVENT_BATCH_SIZE=20
EVENT_BATCH_FLUSH_SECONDS=5
RECOMMENDATION_CACHE_TTL_SECONDS=3600
RECOMMENDATION_BUILD_LOCK_TTL_SECONDS=30
TRAVERSAL_USER_LOCK_TTL_SECONDS=10
CONSENT_CACHE_TTL_SECONDS=60

# === A6 Interest Bayesian (2026-05-17) ===
INTEREST_PROPAGATION_ENABLED=false
INTEREST_BOOST_EXPIRY_ACTIVE_DAYS=14
INTEREST_BATCH_FLUSH_USER_LOCK_TTL_SECONDS=10
DWELL_TICK_CAP_TTL_SECONDS=3600
DWELL_TICK_CAP_PER_DOCUMENT=4
EVENT_DUPLICATE_CACHE_TTL_SECONDS=86400
SYSTEM_CONFIG_REQUIRED=true

# === External ===
OPENALEX_POLITE_EMAIL=dev@insight.test
SEMANTIC_SCHOLAR_API_KEY=
# CSO 3.4 다운로드 URL (A3, decision-backlog P1-5)
CSO_DOWNLOAD_URL=https://cso.kmi.open.ac.uk/downloads/CSO.3.4.csv

# === CORS / hosts ===
CORS_ALLOWED_ORIGINS=http://localhost:3001,app://insight
API_PUBLIC_BASE=http://localhost:8000
LOG_LEVEL=INFO
STRUCTLOG_RENDER=json

# === Worker 병렬 정책 (decision-backlog C-20) ===
UVICORN_WORKERS=1
```

## 검증

부팅 시 `pydantic_settings.BaseSettings`로 모든 변수 검증. 누락 또는 타입 오류 시 즉시 실패. 비밀값은 로그에 마스킹.
