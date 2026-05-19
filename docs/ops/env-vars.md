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
| `LLM_PROVIDER` | **`mock`** (Settings 코드 default — CI 안전) / **`codex_oauth`** (`.env.example` 권고, 시연 default — 사용자 본인 ChatGPT 구독 OAuth) / **`openai`** (정식 API, OPENAI_API_KEY 필수). 2026-05-18 부터 lifespan 가드가 `{mock, openai, codex_oauth}` 화이트리스트 — `anthropic`/`openrouter` 만 NotImplementedError 라 boot 거부. codex_oauth 토글 시 `codex --version` binary 사전 검증 + 호스트 `~/.codex` volume mount + `make codex-login` 1회. |
| `LLM_MODEL_HIGH` | **`gpt-5.5` (default — v13 round 2 사용자 결정)**. mock 일 때도 fixture lookup 이라 model name 무관. 운영자가 OpenAI 의 다른 모델로 토글 가능. | 동적 리프 생성·병합 + search_with_tools |
| `LLM_MODEL_MEDIUM` | **`gpt-5.5` (default — v13 round 2 사용자 결정)**. high/medium 슬롯 모두 동일 모델 (사용자 결정). 토글로 다른 모델 분리 가능. | 요약·추천 이유 |
| `LLM_REASONING_EFFORT_HIGH` | **`high` (default)** | gpt-5/o-series `reasoning_effort` (chat/completions top-level + responses `reasoning.effort`). 가능 값: `none` / `minimal` / `low` / `medium` / `high` / `xhigh`. **xhigh 는 latency + 5h ChatGPT 세션 한도 초과 우려로 미사용** (2026-05-18 사용자 결정). cold-start / leaf identify / trace merge verify / search_with_tools 등 high slot 호출에 적용. 비 reasoning 모델 (gpt-4o 등) 토글 시 자동 제외. |
| `LLM_REASONING_EFFORT_MEDIUM` | **`medium` (default)** | gpt-5/o-series `reasoning_effort` (medium slot). reason 생성 / summary / 일반 medium slot 호출에 적용. |
| `CODEX_OAUTH_TOKEN` | (legacy, 미사용) | 2026-05-18 본문 구현 후 codex CLI 가 `~/.codex/auth.json` 으로 자체 관리 — 본 env 직접 사용 안 함. legacy 호환만. |
| `CODEX_CLI_PATH` | `codex` | `LLM_PROVIDER=codex_oauth` 시 lifespan 이 `<path> --version` 으로 사전 검증. backend Dockerfile 이 `npm i -g @openai/codex` 로 PATH 에 install. |
| `CODEX_SANDBOX_MODE` | `read-only` | codex 가 backend 컨테이너 파일을 임의 mutation 못 하도록 read-only 권장. 가능 값: `read-only` / `workspace-write` / `danger-full-access`. |
| `CODEX_WORKDIR` | `/tmp/codex-runtime` | codex 가 사용할 격리 작업 디렉토리 (`--cd`). git repo 검출 회피. |
| `CODEX_WEB_SEARCH_MODE` | `cached` | search_with_tools 동작 — `cached` (default, codex 자체 cache) / `live` (`--search` flag, 실시간 검색). 시연 안정성 우선이면 cached, 최신성 요구면 live. |
| `CODEX_SERVICE_TIER` | **`fast`** | codex `service_tier` (`-c service_tier=...`). 모든 codex_oauth 호출에 적용 (2026-05-18 사용자 결정). 가능 값: `fast` (우선순위 큐 + 빠른 응답, 시연 latency 최소화) / `default` / `flex` / `scale` / `priority`. 5시간 ChatGPT 세션 한도 안에서 단일 호출 latency 줄이는 게 시연 안정성에 직접 영향. codex 가 모델/요금제 호환 자동 처리. |
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

## A8 Recommendation Engine (2026-05-17 추가)

본 9개 env 는 [`../algorithms/recommendation-ranking.md`](../algorithms/recommendation-ranking.md) + [`../algorithms/cold-start.md`](../algorithms/cold-start.md) 의 운영 토글. 알고리즘 임계 (점수 weight, slot target, fallback 단계) 자체는 `backend/app/config/recommendation.toml` 가 SOR — env 와 TOML 책임 분리 (env: runtime tuning / TOML: 알고리즘 임계). 결정 매트릭스 [`../decisions.md §13`](../decisions.md), decision-backlog C-40.

| Var | 예시 값 | 비고 |
|---|---|---|
| `RECOMMENDATION_BUILD_POLL_TIMEOUT_SECONDS` | `8` | single-flight 폴링 timeout. lock 보유 시 0.2s 폴링 8s 내 cache 결과 대기. 초과 시 직접 build fallback ([`../sdd/concurrency.md §2`](../sdd/concurrency.md)). |
| `RECOMMENDATION_BUILD_POLL_INTERVAL_SECONDS` | `0.2` | polling 주기. |
| `COLD_START_MAX_PER_DAY` | `100` | 전역 일일 cold-start LLM 호출 cap. Redis INCR + EXPIRE 86400. ([`../algorithms/cold-start.md §비용 가드`](../algorithms/cold-start.md)) |
| `COLD_START_MAX_PER_USER_LIFETIME` | `3` | 사용자 lifetime cap (cold-start.md §비용 가드). pseudo_cold_start Document 수 기준 — 초과 시 trust=high 트렌드 fallback. |
| `COLD_START_LLM_TIMEOUT_SECONDS` | `180` | cold-start LLM `complete(high, json)` 호출 timeout. NFR-12 (cache hit 3s) 예외 — 8s SLA 목표지만 cap 180s. 일반 `LLM_REQUEST_TIMEOUT_SECONDS=180` 과 정합. |
| `COLD_START_DEDUP_WINDOW_DAYS` | `30` | cold-start orchestrator 가 pseudo Document 생성 시 기존 Document 매칭 dedup window (A4 `_DEDUP_WINDOW_DAYS` 와 동일). |
| `RATE_LIMIT_DASHBOARD_REFRESH` | `1/minute` | `POST /recommendations/dashboard/refresh` slowapi rate limit. 사용자당 1회/분. |
| `DOCUMENT_SUMMARY_LLM_TIMEOUT_SECONDS` | `60` | `GET /documents/{id}/summary` LLM medium 호출 timeout. DB `DocumentSummaryCache` 가 1차 SOR — 본 timeout 은 miss 시 LLM 한계만. |
| `DOCUMENT_SUMMARY_SOURCE_ABSTRACT_MAX_CHARS` | `500` | LLM 실패 시 generator=`source_abstract` fallback 의 Document.summary 최대 문자 수. |

## A6 Interest Bayesian (2026-05-17 추가)

본 7개 env 는 [`../algorithms/interest-bayesian.md`](../algorithms/interest-bayesian.md) 의 동작 파라미터 — TOML config 가 아닌 env 로 노출되는 운영 토글만 본 표. 베이지안 파라미터 자체 (alpha_prior, half_life 등) 는 system_config 테이블 의 `interest_params` JSONB 행에서 관리 (A10 admin-console 가 UI 제공).

| Var | 예시 값 | 비고 |
|---|---|---|
| `INTEREST_PROPAGATION_ENABLED` | `true` | **(A7, 2026-05-17 머지로 default true)** trace path 조상 1-hop 0.5 propagation 활성. false 로 명시 토글 시 ingest_event 는 직접 토픽 + 부모 cso_topic_id 만 갱신. |
| `INTEREST_BOOST_EXPIRY_ACTIVE_DAYS` | `14` | onboarding boost (cluster +1.0 / 1-hop child +0.5) 가 자연 만료되는 active day. decay cron 이 `user.active_day_counter - boost_applied_at_active_day >= N` row 의 alpha 에서 boost 분 차감 + 컬럼 NULL 화. |
| `INTEREST_BATCH_FLUSH_USER_LOCK_TTL_SECONDS` | `10` | EventBuffer flush 시 per-user mutex TTL (초). flush 후 즉시 release. traversal_lock 과 별도. |
| `DWELL_TICK_CAP_TTL_SECONDS` | `3600` | dwell_tick Redis 카운터 TTL (초). 문서당 cap 4회 도달 후 1시간 자연 소멸. |
| `DWELL_TICK_CAP_PER_DOCUMENT` | `4` | dwell_tick 문서당 베이지안 갱신 cap (30s×4=2분). cap 초과 시 베이지안 skip, active_day 와 UserEvent INSERT 는 그대로. SRS 체류 ≥2분 기준 정렬. |
| `EVENT_DUPLICATE_CACHE_TTL_SECONDS` | `86400` | event idempotency payload-hash Redis 캐시 TTL (초). DB UNIQUE(user_id, client_request_id) 가 1차 SOR — 본 캐시는 응답 RTT 단축용. |
| `SYSTEM_CONFIG_REQUIRED` | `true` | **(Codex S-05 fix)** lifespan startup 시 system_config seed (interest_params, event_weights) 누락 동작. `true` (default 운영) → RuntimeError 로 startup 차단 (fail-fast). `false` (테스트 / 의도적 비활성) → WARN + endpoint fallback. |

## A7 Leaf Lifecycle + Traversal (2026-05-17 추가)

본 33개 env 는 [`../algorithms/leaf-topic-lifecycle.md`](../algorithms/leaf-topic-lifecycle.md) + [`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md) 의 동작 파라미터. `app/config/topic_lifecycle.toml` 가 동시에 같은 임계를 보유 — env 가 TOML 을 override 한다 (운영 시 hot toggle 용). PR-1 contracts 추가 결정 매트릭스 23건 ([`../decisions.md §12`](../decisions.md)).

### Leaf 식별 (emerging)

| Var | 예시 값 | 비고 |
|---|---|---|
| `LEAF_LIFECYCLE_CRON` | `30 3 * * *` | A7 신규 (결정 #14). collection cron 직후 ~30분 시점에 사용자별 `identify_emerging` LLM 호출 cron. COLLECTION_CRON 변경 시 본 cron 도 +30분 으로 동기화. |
| `LEAF_EMERGING_MAX_PER_DAY` | `3` | LLM `identify_emerging` 가 일일 사용자별 채택하는 최대 emerging 후보 수. confidence 내림차순 상위 N. |
| `LEAF_EMERGING_CONFIDENCE_MIN` | `0.6` | Strict 검증 — candidate confidence 미달 자동 거부. |
| `LEAF_EMERGING_SUPPORTING_DOCUMENTS_MIN` | `3` | Strict 검증 — supporting_document_ids 길이 미달 자동 거부. |
| `LEAF_EMERGING_LABEL_SIMILARITY_DEDUP` | `0.75` | 기존 active leaf 라벨 의미유사도 ≥ 임계 시 dedup (신규 거부). Levenshtein 정규화 사용 (임베딩 미사용). |
| `LEAF_EMERGING_INPUT_WINDOW_HOURS` | `24` | LLM input 시간 window. A4 collection 결과 + UserEvent click/save Document 의 union (결정 매트릭스 #18 옵션 D). |
| `LEAF_LLM_ANCHOR_RETRY_CAP` | `1` | trace_anchor_required 위반 candidate 모두 거부 시 보강된 prompt 로 재호출 cap. 2차도 위반 시 빈 응답 fallback + warning log. |
| `LEAF_LIFECYCLE_LOCK_TTL_SECONDS` | `60` | daily emerging 식별 cron 의 per-user mutex TTL (`RedisKey.leaf_lifecycle_lock`). |

### Leaf 룰 기반 전이

| Var | 예시 값 | 비고 |
|---|---|---|
| `LEAF_ACTIVE_WINDOW_DAYS` | `7` | emerging → active 승격 window (active days). |
| `LEAF_ACTIVE_MIN_DOCUMENTS` | `5` | 승격 임계 — window 내 매핑 Document ≥ N. |
| `LEAF_ACTIVE_MIN_INTEREST_SIGNALS` | `2` | 승격 임계 — window 내 click/save ≥ N. |
| `LEAF_STALE_IDLE_DAYS` | `21` | active → stale 강등 임계 (idle active days). |
| `LEAF_ARCHIVED_IDLE_DAYS` | `90` | stale → archived 폐기 임계 (idle active days). |
| `LEAF_EMERGING_ARCHIVED_IDLE_DAYS` | `14` | emerging → archived 폐기 (승격 전 idle 만료). |
| `LEAF_REACTIVATION_WINDOW_DAYS` | `7` | stale → active 재활성화 window. |
| `LEAF_REACTIVATION_MIN_DOCUMENTS` | `3` | 재활성화 임계 — window 내 매핑 Document ≥ N. |
| `LEAF_REACTIVATION_MIN_INTEREST_SIGNALS` | `1` | 재활성화 임계 — window 내 click/save ≥ N. |

### Leaf 병합 (LLM 주 1회)

| Var | 예시 값 | 비고 |
|---|---|---|
| `LEAF_MERGE_JACCARD_MIN` | `0.6` | 두 leaf 의 문서 Jaccard ≥ 임계 시 merge 후보. |
| `LEAF_MERGE_LABEL_SIMILARITY_MIN` | `0.75` | 두 leaf 의 라벨 의미유사도 ≥ 임계 시 merge 후보. |
| `LEAF_MERGE_MAX_PER_USER` | `50` | 주간 평가 시 사용자당 evaluate 최대 leaf 수 (LLM context 보호). |
| `MERGE_EVALUATION_LOCK_TTL_SECONDS` | `120` | 주간 leaf 병합 cron 의 per-user mutex TTL (`RedisKey.merge_evaluation_lock`). |

### Trace operation (extend / retract / split / archive / **merge**)

trace operation 4 → 5 로 확장 (merge 신규 도입, decisions.md §12 결정 #17).

| Var | 예시 값 | 비고 |
|---|---|---|
| `TRACE_ACTIVE_CAP` | `10` | 사용자당 active trace 최대 수. 초과 시 새 trace 생성 거부. |
| `TRACE_PATH_DEPTH_CAP` | `8` | trace.path 최대 깊이. extend 시 cap 초과 차단. |
| `TRACE_STALE_IDLE_DAYS` | `21` | 1단계 stale 마킹 — path 말단 score_tail ≤ 임계 AND idle ≥ N active days (ingest 직후 즉시, no LLM). |
| `TRACE_STALE_THRESHOLD_SCORE` | `0.30` | stale 마킹 score 임계. |
| `TRACE_RETRACT_AFTER_STALE_DAYS` | `14` | 2단계 retract — stale 누적 추가 N active days → daily cron 시 LLM 재배치 + path.pop. |
| `TRACE_ARCHIVE_AFTER_STALE_DAYS` | `90` | 3단계 archive — stale 누적 N active days → status='archived' (no LLM). |
| `TRACE_EXTEND_MIN_INTERACTIONS` | `5` | extend operation 트리거 — 자식 노드 인터랙션 ≥ N. |
| `TRACE_SPLIT_WINDOW_DAYS` | `7` | split operation window — 두 자식 동시 extend 임계 도달 (active days). split 후 T 단축 + T'=분기점+B (결정 매트릭스 #20). |
| `TRACE_MERGE_PATH_OVERLAP_MIN` | `3` | **(A7 신규)** trace merge 룰 trigger — 두 active trace path 가 같은 cso_topic_id ≥ N 공유 OR 한 path 가 다른 path 의 proper subset → LLM 검증 후 merge. |
| `TRACE_MERGE_CRON` | `0 18 * * *` | **(A7 신규)** daily trace merge cron. 18 UTC = 03 KST (A6 INTEREST_DECAY_CRON 과 같은 시각 — user-mutex 공유). |
| `TRACE_MERGE_LOCK_TTL_SECONDS` | `120` | trace merge cron 의 per-user mutex TTL (`RedisKey.trace_merge_lock`). LLM 호출 동반이라 decay 보다 길게. |

### Propagation (cso-topic-traversal.md §4)

| Var | 예시 값 | 비고 |
|---|---|---|
| `PROPAGATION_HOP_DECAY` | `0.5` | path 위 조상 노드로 N-hop 감쇠 factor. |
| `PROPAGATION_MAX_HOPS` | `4` | propagation 최대 hop 깊이. |

## A8-v2 UserProfile + Discovery Fusion + Reincarnation (2026-05-19 추가)

[`decisions.md §15`](../decisions.md) 결정 매트릭스 + [`../algorithms/recommendation-ranking.md`](../algorithms/recommendation-ranking.md) Discovery 섹션. discovery slot 2 (Fusion 1 + Reincarnation 1) 의 input SOR. daily LLM cron 이 사용자별 1회 archive×current cross-product 융합 + reincarnation seed 생성.

| Var | 예시 값 | 비고 |
|---|---|---|
| `USER_PROFILE_CRON` | `0 19 * * *` | **(A8-v2 신규)** daily user_profile cron. 19 UTC — A6/A7 18 UTC 와 분리 (user-mutex 충돌 회피). |
| `USER_PROFILE_ARCHIVE_SCORE_TAIL_MIN` | `0.6` | **(A8-v2 신규)** reincarnation candidate 풀에 들어갈 archived trace 의 score_tail 최소 임계. 강한 신호로 끝난 archive 만 후보 — 자연 둔화 archive 는 노이즈 제외. |
| `USER_PROFILE_GENERATOR_VERSION` | `v1` | **(A8-v2 신규)** prompt template + output schema 버전 추적. `UserProfile.generator_version` 컬럼 값. 변경 시 bump → daily cron 매일 갱신이라 자연 교체. |
| `USER_PROFILE_INPUT_ARCHIVE_MAX` | `8` | **(A8-v2 신규)** LLM input archive 상한 (token 폭주 가드). score_tail DESC 정렬 상위 N. |
| `USER_PROFILE_REINCARNATION_GAP_DAYS_MIN` | `7` | **(A8-v2 신규)** archived_at 직후 본 active day 미만 archive 는 reincarnation 후보 제외 — 자연 망각 시간 부재. |
| `USER_PROFILE_LOCK_TTL_SECONDS` | `360` | **(A8-v2 신규)** daily cron `RedisKey.user_profile_generation_lock` TTL. LLM 호출 동반이라 2x LLM timeout 마진 (Codex R1 Critical #1 fix 2026-05-19 — 직전 180=LLM timeout 였음). |
| `USER_PROFILE_CACHE_TTL_SECONDS` | `3600` | **(A8-v2 신규)** `RedisKey.user_profile_cache` SETEX TTL — engine.build_dashboard fetch 후 1h. daily cron 완료 시 DEL invalidate. |

## 외부 소스 키 (있을 때만 채움)

> **v13 라운드 박스 (2026-05-11)**: A4 Topic-driven Pivot ([`../decisions.md §10`](../decisions.md))으로 6 source 어댑터 폐기. `OPENALEX_POLITE_EMAIL` / `SEMANTIC_SCHOLAR_API_KEY` 는 본 라운드 시점 **dead code** (어댑터 없음). config 에서 즉시 제거하지는 않고 — 향후 어댑터 도입 시 재사용 가능성 위해 보존. **활성 외부 키**: LLM provider 별 API key (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` — §LLM 섹션 참조) + `CSO_DOWNLOAD_URL`.

| Var | 예시 값 | 비고 |
|---|---|---|
| ~~`OPENALEX_POLITE_EMAIL`~~ | ~~`dev@insight.test`~~ | **(v13 라운드 dead)** OpenAlex 어댑터 미구현. 본 env 미사용. |
| ~~`SEMANTIC_SCHOLAR_API_KEY`~~ | ~~(선택)~~ | **(v13 라운드 dead)** Semantic Scholar 어댑터 미구현. 본 env 미사용. |
| `CSO_DOWNLOAD_URL` | `https://cso.kmi.open.ac.uk/downloads/CSO.3.4.1.csv` | A3 (cso-topic engine). `scripts/import_cso.py` 다운로드 URL. 캐시 파일명 = URL basename (`CSO.3.4.1.csv`). 호스트에 미리 받은 CSV 는 `make seed-cso-cache FILE=...` 로 `cso_cache` volume 에 카피해 URL 다운로드 skip. decision-backlog P1-5 — 버전 갱신(3.5+) 시 본 env 만 교체 + `make import-cso ARGS="--reset --refresh"`. |

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
# 시연 default 권고 = codex_oauth (사용자 본인 ChatGPT 구독 OAuth, 비용 0).
# 호스트 `make codex-login` 1회 후 docker compose up. CI 환경은 LLM_PROVIDER=mock 으로 override.
LLM_PROVIDER=codex_oauth
LLM_MODEL_HIGH=gpt-5.5
LLM_MODEL_MEDIUM=gpt-5.5
# OpenAI reasoning_effort (gpt-5/o-series). high/medium 슬롯 분리 (2026-05-18 fix).
# 가능 값: none, minimal, low, medium, high, xhigh.
# xhigh 는 latency + 5h ChatGPT 세션 한도 초과 우려로 미사용 (사용자 결정).
LLM_REASONING_EFFORT_HIGH=high
LLM_REASONING_EFFORT_MEDIUM=medium
# 정식 API로 토글하려면 위 LLM_PROVIDER=openai 후 아래 키 채움
OPENAI_API_KEY=
# CodexOAuthProvider (LLM_PROVIDER=codex_oauth 토글 시, 2026-05-18 본문) —
# 사용자 본인 ChatGPT 구독 OAuth 활용. 호스트에서 `make codex-login` 1회 필요.
CODEX_CLI_PATH=codex
CODEX_SANDBOX_MODE=read-only
CODEX_WORKDIR=/tmp/codex-runtime
CODEX_WEB_SEARCH_MODE=cached
CODEX_SERVICE_TIER=fast
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

# === A8 Recommendation Engine (2026-05-17) ===
RECOMMENDATION_BUILD_POLL_TIMEOUT_SECONDS=8
RECOMMENDATION_BUILD_POLL_INTERVAL_SECONDS=0.2
COLD_START_MAX_PER_DAY=100
COLD_START_MAX_PER_USER_LIFETIME=3
COLD_START_LLM_TIMEOUT_SECONDS=180
COLD_START_DEDUP_WINDOW_DAYS=30
RATE_LIMIT_DASHBOARD_REFRESH=1/minute
DOCUMENT_SUMMARY_LLM_TIMEOUT_SECONDS=60
DOCUMENT_SUMMARY_SOURCE_ABSTRACT_MAX_CHARS=500

# === A6 Interest Bayesian (2026-05-17) ===
INTEREST_PROPAGATION_ENABLED=true
INTEREST_BOOST_EXPIRY_ACTIVE_DAYS=14
INTEREST_BATCH_FLUSH_USER_LOCK_TTL_SECONDS=10
DWELL_TICK_CAP_TTL_SECONDS=3600
DWELL_TICK_CAP_PER_DOCUMENT=4
EVENT_DUPLICATE_CACHE_TTL_SECONDS=86400
SYSTEM_CONFIG_REQUIRED=true

# === A7 Leaf Lifecycle + Traversal (2026-05-17) ===
LEAF_EMERGING_MAX_PER_DAY=3
LEAF_EMERGING_CONFIDENCE_MIN=0.6
LEAF_EMERGING_SUPPORTING_DOCUMENTS_MIN=3
LEAF_EMERGING_LABEL_SIMILARITY_DEDUP=0.75
LEAF_EMERGING_INPUT_WINDOW_HOURS=24
LEAF_LLM_ANCHOR_RETRY_CAP=1
LEAF_LIFECYCLE_LOCK_TTL_SECONDS=60
LEAF_ACTIVE_WINDOW_DAYS=7
LEAF_ACTIVE_MIN_DOCUMENTS=5
LEAF_ACTIVE_MIN_INTEREST_SIGNALS=2
LEAF_STALE_IDLE_DAYS=21
LEAF_ARCHIVED_IDLE_DAYS=90
LEAF_EMERGING_ARCHIVED_IDLE_DAYS=14
LEAF_REACTIVATION_WINDOW_DAYS=7
LEAF_REACTIVATION_MIN_DOCUMENTS=3
LEAF_REACTIVATION_MIN_INTEREST_SIGNALS=1
LEAF_MERGE_JACCARD_MIN=0.6
LEAF_MERGE_LABEL_SIMILARITY_MIN=0.75
LEAF_MERGE_MAX_PER_USER=50
MERGE_EVALUATION_LOCK_TTL_SECONDS=120
TRACE_ACTIVE_CAP=10
TRACE_PATH_DEPTH_CAP=8
TRACE_STALE_IDLE_DAYS=21
TRACE_STALE_THRESHOLD_SCORE=0.30
TRACE_RETRACT_AFTER_STALE_DAYS=14
TRACE_ARCHIVE_AFTER_STALE_DAYS=90
TRACE_EXTEND_MIN_INTERACTIONS=5
TRACE_SPLIT_WINDOW_DAYS=7
TRACE_MERGE_PATH_OVERLAP_MIN=3
TRACE_MERGE_CRON=0 18 * * *
TRACE_MERGE_LOCK_TTL_SECONDS=120
PROPAGATION_HOP_DECAY=0.5
PROPAGATION_MAX_HOPS=4

# === A8-v2 UserProfile + Discovery Fusion + Reincarnation (2026-05-19) ===
USER_PROFILE_CRON=0 19 * * *
USER_PROFILE_ARCHIVE_SCORE_TAIL_MIN=0.6
USER_PROFILE_GENERATOR_VERSION=v1
USER_PROFILE_INPUT_ARCHIVE_MAX=8
USER_PROFILE_REINCARNATION_GAP_DAYS_MIN=7
USER_PROFILE_LOCK_TTL_SECONDS=180
USER_PROFILE_CACHE_TTL_SECONDS=3600

# === External ===
OPENALEX_POLITE_EMAIL=dev@insight.test
SEMANTIC_SCHOLAR_API_KEY=
# CSO 3.4.1 다운로드 URL (A3, decision-backlog P1-5)
CSO_DOWNLOAD_URL=https://cso.kmi.open.ac.uk/downloads/CSO.3.4.1.csv

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
