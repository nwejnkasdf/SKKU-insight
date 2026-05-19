# SKKU InSight — Agents Guide

본 파일은 **모든 후속 에이전트(Claude / Codex / 사람 협업자)**가 본 저장소에서 작업을 시작할 때 가장 먼저 읽는 진입점이다. 모델·도구 종속 표현은 회피하므로 Claude·Codex·사람이 동일하게 해석할 수 있다.

> **사람이 읽는 프로젝트 소개**는 [`README.md`](README.md). 본 파일은 에이전트(코드 작성자) 전용 운영 헌법이다.

## 프로젝트 한 단락

`SKKU InSight`는 이공계 학생·연구자·교수가 직접 검색하지 않아도 자기 관심에 맞는 CS/AI 기술 동향을 선제적으로 받아볼 수 있는 **Windows 데스크톱 애플리케이션**이다. **사용자별 CSO 그래프 traversal trace**가 관심 상태의 단위이고, 그 위에 **사용자별 dynamic leaf 토픽**이 분기되며, 추천은 **current/adjacent/proactive 3 카테고리 ↔ core/adjacent/discovery 슬롯 1:1 매핑**이다. 백엔드 FastAPI + PostgreSQL/Redis, 클라이언트 Electron+React+TS, 관리자 Next.js, 모두 단일 `docker-compose.yml`로 기동. 성균관대 소프트웨어공학개론 조별과제 산출물이며 1차 목표는 **풀스택 동작 데모**(10-20명 동시 사용자 가정).

## 첫 30분 — 4개 진입 문서

새 에이전트는 다음 4개를 순서대로 읽으면 작업 시작 가능.

1. **[`docs/decisions.md`](docs/decisions.md)** — 13 라운드 결정 매트릭스 압축본 (v13 = A4 Topic-driven Pivot, 2026-05-11). 모든 코드 결정의 단일 진실 공급원. SRS와 충돌 시 본 파일이 우선 (단 SRS의 FR/NFR/AT 식별자·표는 보존).
2. **[`docs/decision-backlog.md`](docs/decision-backlog.md)** — P0/P1/P2 + C-급 백로그. **P0 0건. P1 활성 7건 + P2 활성 5건은 default·stub 경로 정의됨. C-급 38건 모두 해소** (A2 자체 검수 + Codex review v1·v2·v3 + multi-worker + 옵션 B + mypy strict 26 + 초기 결정 11 + A4 코드 + 3-라운드 audit + A6 본문 + 2-라운드 audit, 2026-05-11~17).
3. **[`docs/sdd/contracts.md`](docs/sdd/contracts.md)** + **[`docs/sdd/agent-orchestration.md`](docs/sdd/agent-orchestration.md)** — 모든 enum·error code·Redis key는 `backend/app/contracts.py` 단일 SOR. 멀티 에이전트 5겹 방어와 Phase별 순차 호출 룰.
4. **자기 모듈에 해당하는 `docs/` 하위 디렉토리** — 후술 §에이전트 분할 표.

원본 SRS(IEEE 830)는 [`docs/srs/`](docs/srs/)에 분할 보존. 새 변경 가하지 말 것.

## 디렉토리 지도

```
.
├── README.md                          # 사람이 읽는 프로젝트 소개
├── AGENTS.md                          # 본 파일 — 에이전트 진입점
├── CLAUDE.md                          # AGENTS.md로 redirect 한 장
├── SKKU_InSight_SRS.{md,docx,pdf}     # 원본 SRS v0.3 (보존)
├── docker-compose.yml                # ✅ A2: 5 서비스 (postgres/redis/api/worker/admin-console)
├── Makefile                          # ✅ A2 + A3 (import-cso): dev/migrate/create-admin/import-cso/test/lint/check-all/clean
├── .env.example                      # ✅ A2 + A3 + A6: env-vars.md 골격 + NAVER_CLEANUP_CRON + UVICORN_WORKERS + CSO_DOWNLOAD_URL + INTEREST_* 7 (A6)
├── docs/                              # 산출 문서 54+ 파일
│   ├── README.md                      # docs 인덱스
│   ├── decisions.md                   # 결정 매트릭스 (SOR)
│   ├── decision-backlog.md            # P0/P1/P2 + C-급 (A2 후 32 해소 / A3 11 / A4 26 / A6 12 신규 → 누적 38)
│   ├── srs/                           # SRS 분할본 10개
│   ├── sdd/                           # 9: 아키텍처·데이터 흐름·배포·모듈 경계·기술 스택·동시성·API 규약·계약·에이전트 오케스트레이션
│   ├── api/        (8)                # auth/consent/onboarding/topics/interest/collection/recommendation/admin — A6: interest.md 오류 표 갱신
│   ├── algorithms/ (7)                # interest-bayesian/cso-topic-traversal/leaf-topic-lifecycle/recommendation-ranking/cold-start/clickbait-integration/cso-mapping
│   ├── data/       (5)                # schema/erd/sources-registry/cso-import/seed-personas — A6: schema SystemConfig § 신규
│   ├── ops/        (5)                # docker-compose/env-vars/ci-cd/admin-bootstrap/runbooks — A6: env-vars INTEREST_* 7 + SYSTEM_CONFIG_REQUIRED
│   ├── security/   (5)                # auth-flow/token-handling/rate-limiting/password-policy/threat-model
│   └── ux/         (4)                # wireframes/ui-states/i18n/client-behaviors
├── backend/                          # ✅ A2 + A3 + A4 + A6 본문 완료
│   ├── Dockerfile                    # python:3.12-slim
│   ├── pyproject.toml                # FastAPI + Pydantic + SQLAlchemy async + bcrypt + rq + rq-scheduler + slowapi + structlog + httpx + networkx (A3 mypy override 추가)
│   ├── .env.example                  # backend 단독 부트용 (compose 미사용 시) + A6: INTEREST_* 7 신규
│   ├── alembic.ini + alembic/env.py + versions/{0001_initial_a2_tables, 0002_a3_cso_traversal_leaf, 0003_a4_collection_tables, 0004_a6_interest_tables}.py
│   ├── app/
│   │   ├── main.py + lifespan.py + redis.py + contracts.py    # ✅ A6: lifespan system_config 로더 + EventBuffer task. contracts JobType.INTEREST_DECAY + ErrorCode 2 + RedisKey 3 신규
│   │   ├── config/                                       # ✅ A3 패키지화 + A6: BaseSettings INTEREST_* 7 신규 field
│   │   ├── db/{base, engine, session, models/*}          # ✅ 21 모델 — A2 8 + A3 3 + A4 4 + A6 6 (UserEvent/UserInterestState/SavedDocument/HiddenDocument/NotInterestedTopic/SystemConfig)
│   │   ├── security/{password, jwt, rate_limit, consent_cache, idempotency, deps}  # + common_passwords.txt
│   │   ├── auth/, consent/, onboarding/, admin/          # 17 endpoint 본문 (service + router) — A6: onboarding/service.py bootstrap_interest_state 협업 추가
│   │   ├── topic/                                        # ✅ A3 본문 완료 — 9 파일 + 7 endpoint
│   │   ├── collection/                                   # ✅ A4 본문 완료 (v13 라운드 pivot)
│   │   ├── interest/                                     # ✅ A6 본문 완료 — bucket/config_loader/decay/idempotency/propagation/router/schemas/service/topic_distribution (9 파일) + 9 endpoint
│   │   ├── events/                                       # ✅ A6 신규 — buffer (5초 batch flush) + active_day (atomic counter)
│   │   ├── recommendation/                               # stub 유지 (A8)
│   │   ├── middleware/{request_id, jwt_auth, consent_gate, exception_handler, structlog_mask}
│   │   ├── llm_provider/{protocol, mock, openai, anthropic, openrouter, codex_oauth, _concurrency}  # Redis 분산 semaphore
│   │   ├── worker.py + scheduler.py + worker/jobs/{account_deletion(완료), cold_start/naver_cleanup/collection(A4)/interest_decay(A6 완료)/merge_evaluation(stub)}
│   ├── scripts/{create_admin, reset_password, export_openapi, import_cso}
│   └── tests/{conftest + security/admin/llm_provider/auth/consent/topic/collection + interest/ (12 + audit_regressions A6 9 회귀)}    # ✅ A6: 13 신규 (~146 total)
├── scripts/                          # ✅ A2: 6 cross-check (api_docs / schema / env / error_codes / redis_keys / contracts) + _common.py
├── prompts/                          # 에이전트별 kickoff prompts (A1 ✅ / A2-stub ✅ / A2 ✅ / A3 ✅ / A4 ✅ / A5 ✅ / A6 ✅ / 나머지 ⬜)
├── clickbait_module/                 # ✅ vLLM + DoRA 분류기 자체 서비스 (P0-1 해결)
└── (이하 후속 에이전트가 만듦)
    client/  admin-console/  .github/
```

## 핵심 결정 매트릭스 (압축)

| 영역 | 결정 |
|---|---|
| 산출물 형태 | 풀스택 동작 데모 (10-20명 동시), Docker Compose 단일 머신 |
| 클라이언트 | Electron + React + TypeScript |
| 관리자 콘솔 | Next.js |
| 백엔드 | FastAPI(Python 3.12) + Pydantic v2 + SQLAlchemy 2.x async |
| DB | PostgreSQL 16 + Redis 7 |
| 인증 | JWT Access(15m) + Refresh(Redis 14d) + bcrypt(12) + Electron `safeStorage` |
| **사용자 관심 모델** | **CSO 그래프 traversal trace** (path 자체가 관심 상태). 행동이 root, 명시 선택은 14 active day 한정 prior boost |
| **카테고리 ↔ 슬롯** | **current/adjacent/proactive ↔ core/adjacent/discovery 1:1**. core 5/adj 3/disc 2 |
| 시간 단위 | **모든 N일 임계는 active day** (사용자 인터랙션 1+건 있는 날 단조증가 카운터) |
| 베이지안 | Beta-Bernoulli, 단기 t1/2=7 active days, 장기 60. atomic SQL UPSERT. 1-hop 0.5 propagation |
| Trace operation | extend/retract/split/archive 룰 기반. LLM은 leaf 재배치에만 (retract/split). **3단계 강등** active→stale→retract→archive |
| Leaf 라이프사이클 | D 하이브리드 (신규 식별·병합만 LLM, 승격·강등 룰). emerging는 active trace path 끝 산하에서만 분기. core 슬롯 5개 중 1개 emerging quota |
| LLM 어댑터 | **`MockProvider`** (Settings 코드 default, CI 안전) / **`CodexOAuthProvider`** (`.env.example` 권고 시연 default — 2026-05-18 본문, `codex exec --json` subprocess wrap, 사용자 본인 ChatGPT 구독 OAuth) / `OpenAIAPIProvider` (정식 API) / Anthropic·OpenRouter stub. 환경변수 `LLM_PROVIDER` 토글. 모든 slot 모델 = `gpt-5.5`, slot 구분 = `reasoning_effort` (high/medium, xhigh 미사용) |
| 낚시성 | DoRA 파인튜닝 `A.X-4.0-Light` 모듈 (✅ `clickbait_module/` 자체 vLLM 서비스, P0-1 해결 2026-05-11) |
| 임베딩 | **미사용**. 토픽 유사도는 CSO 그래프 거리, 중복 제거는 URL/DOI/제목 정규화 + Levenshtein |
| 수집 모델 | **(v13 라운드 pivot, 2026-05-11)** `LLMProvider.search_with_tools()` 단일 경로 (web 검색 도구). user trace JSON 입력 → LLM 자율 query → Document INSERT. Source 테이블 = sentinel `llm_search` + `cold_start_pseudo` 2행. publisher 정보는 `Document.raw` JSONB. 6 source 어댑터(arXiv/OpenAlex/S2/DBLP/RSS/네이버BS4) **폐기**. 자세히는 [`docs/decisions.md §10`](docs/decisions.md). |
| 시드 | 5+ 페르소나 + 14일치 인터랙션 (active day 기반) |
| 동시성 | 10-20명 가정. single-flight + user-mutex + atomic SQL + **LLM Redis 분산 semaphore** (multi-worker 안전, C-19) + batch flush + consent cache + jitter |
| Worker 정책 | `UVICORN_WORKERS=1` default. N>1 시 DB pool 합산 = N × `PG_API_POOL_MAX` + `PG_WORKER_POOL_MAX` ≤ PostgreSQL `max_connections` (C-20) |
| Refresh rotation | Redis Lua `_LUA_VERIFY_ROTATE_ISSUE` (verify + mark `:rotated` + new meta INSERT + new index SET 단일 atomic, C-21). HMAC `:rotated` 마커로 family revoke |

## 작업 규칙 (모든 에이전트 공통, 14조)

1. **본문 한국어, 코드/CLI/식별자 영어**. 변수·함수·테이블 snake_case.
2. **FR-XX·NFR-XX·AT-XX·UC-XX는 SRS 표기 그대로**. 새 식별자 만들지 말 것.
3. **결정은 [`docs/decisions.md`](docs/decisions.md) 우선**. SRS와 충돌 시 그쪽 우선이지만 SRS 식별자·표는 보존.
4. **모델 종속 회피**. `MockProvider` (CI default) / `CodexOAuthProvider` (시연 default, 2026-05-18 본문) / `OpenAIAPIProvider` (정식 API) 모두에서 동일 동작.
5. **이미지 자산 부재**. SRS 분할의 `assets/*.png` 링크는 IEEE 830 원형 보존 목적의 죽은 링크. 와이어프레임 SOR은 [`docs/ux/wireframes.md`](docs/ux/wireframes.md), ERD는 [`docs/data/erd.mmd`](docs/data/erd.mmd).
6. **새 기능 임의 추가 금지**. SRS·본 가이드에 없으면 [`docs/decision-backlog.md`](docs/decision-backlog.md) P2로 추가 후 사용자 승인.
7. **TODO 마커**: `<!-- TODO: ... -->` 표기 + 동시에 `decision-backlog.md` 항목 추가.
8. **테스트**: pytest(backend) + vitest(client/admin) + GitHub Actions. AT-01~15 자동화 가능 항목은 [`docs/srs/08-acceptance-tests.md`](docs/srs/08-acceptance-tests.md) 표.
9. **시연 모드 default**: `LLM_PROVIDER=codex_oauth` (`.env.example` 권고 — 사용자 본인 ChatGPT 구독 OAuth, 시연 부트 전 `make codex-login` 1회). CI 환경은 `LLM_PROVIDER=mock` override (Settings 코드 default 가 mock 이라 .env 없이도 부트 가능).
10. **동시성 가드**: [`docs/sdd/concurrency.md`](docs/sdd/concurrency.md) §10 체크리스트 통과 필수. single-flight + user-mutex + atomic SQL + LLM semaphore + batch flush + consent cache.
11. **API 통신 규약**: [`docs/sdd/api-conventions.md`](docs/sdd/api-conventions.md) 따름. JSON·헤더·ErrorResponse·PagedResponse cursor envelope·idempotency·rate limit·CORS·OpenAPI cross-check.
12. **Contracts SOR**: 모든 enum·error code·Redis key·base 모델은 `backend/app/contracts.py`만이 정의 ([`docs/sdd/contracts.md`](docs/sdd/contracts.md)). 에이전트는 import만, 새 항목 정의 금지. 추가는 별도 contracts PR + 사용자 승인.
13. **에이전트 헌법** ([`docs/sdd/agent-orchestration.md §2`](docs/sdd/agent-orchestration.md)): 다른 모듈 시그니처 정의 X, OpenAPI codegen 결과 import만, DB schema 변경은 alembic + docs 동시, 자기 모듈 외 파일 수정 시 PR description에 명시.
14. **Phase별 순차 호출** ([`docs/sdd/agent-orchestration.md §3`](docs/sdd/agent-orchestration.md)): Phase 0a는 contracts.py + endpoint stub 전용 단일 세션. 사용자 검수 + OpenAPI codegen 후에 다른 Phase 시작.

## 5겹 방어 — 멀티 에이전트 안전장치

핵심 설계 우려는 **에이전트들 사이 통신 규격 표류**. 사용자 인지에 의존하지 않고 자동 차단:

| Layer | 도구 | 차단 대상 |
|---|---|---|
| 1. Contract-first | `backend/app/contracts.py` (사용자 1회 작성, 모든 에이전트 import만) | enum 표류, error code 차이, Redis key 컨벤션 |
| 2. OpenAPI codegen | `scripts/export_openapi.py` → `client/src/generated/api.ts`, `admin-console/src/generated/api.ts` | API 시그니처 표류 |
| 3. Cross-check 6종 | `scripts/check_{api_docs,schema,env,error_codes,redis_keys,contracts}.py` (CI 강제) | docs ↔ 코드 drift |
| 4. Strict type | `mypy --strict` + `ruff` + `tsc --strict` | 함수 시그니처 mismatch |
| 5. Phase 순차 호출 | Phase 0a stub → 검수 → 후속 Phase | 병렬 race로 인한 인터페이스 충돌 |

자세히는 [`docs/sdd/agent-orchestration.md`](docs/sdd/agent-orchestration.md).

## 에이전트 분할 (Phase 0a~4, A1·A2-stub·A2~A12)

각 에이전트는 **자기 디렉토리 + 의존 인접 인터페이스만** 컨텍스트로 받는다.

| Phase | ID | 산출 | 상태 |
|---|---|---|---|
| 0a (게이트) | **A1 docs-bootstrap** | 본 `docs/` 54+ 파일 | ✅ 완료 |
| 0a (게이트) | **A2-stub** | `backend/app/contracts.py`, 53 endpoint signature stub, Pydantic schemas, `scripts/export_openapi.py` | ✅ 완료 ([PR #4](https://github.com/nwejnkasdf/SKKU-insight/pull/4)) |
| 0b | **A2 backend-foundation** | A2-stub 본문 17 endpoint + Alembic 1번 migration (8 테이블) + 보안·동시성·LLM·worker·scheduler·middleware·tests·docker-compose·Makefile·6 cross-check | ✅ 완료 ([PR #7](https://github.com/nwejnkasdf/SKKU-insight/pull/7) — 35건 결함 해소, `ruff`·`mypy --strict`·`pytest`·6 check 통과) |
| 0b | **A3 cso-topic** | CSO 3.4 임포트 (`scripts/import_cso.py`) + NetworkX 메모리 캐시 + BroadInterest 12행 시드 (`config/broad_interests.toml`) + 7 endpoint 본문 (`/documents` 1개만 A4·A8 의존 NotImplementedError) + alembic 0002 (cso_topic_parent · dynamic_leaf_topic · dynamic_leaf_topic_cso_topic) + ORM 11 모델 | ✅ 완료 (5 PR-stack: docs-drift 70f077d + A2 ORM hotfix d1663d7 + A3 본문 645d450 + 자체감사 fix 57ef185 + Codex 감사 fix ba10e10 + Codex 재감사 fix 8bb7062 — `ruff`·`mypy --strict 100 files`·`pytest 65/65`·6 check 통과. 3 라운드 독립 감사 Critical 6 fix + Suggested 9 fix + 11 신규 backlog) |
| 1 | **A4 collection** | **(v13 라운드 pivot)** LLM tool-use 검색 + Document/DocumentTopic/CollectionJob 영속 + dedup + jitter + `/topics/{id}/documents` 본문 (cross-cutting) | ✅ **시연 검증 완료 (2026-05-16, docker compose + 실 OpenAI GPT-5.5 호출)** — 26 documents inserted (33 academic_paper + 14 vendor_blog 정상 분류), NFR-25 self-summary 100% 준수, dedup cross-leaf 정상. round 1: alembic 0003 + ORM 4 + LLMProvider.search_with_tools + collection/{llm_search, dedup, orchestrator, service, router} + topic/documents_service + worker/collection + scheduler + CLICKBAIT_ENABLED env. **round 2 Codex fix 15건**: C-01 (OpenAI Responses API, GPT-5.5) / C-02 (dedup 2그룹) / C-03 (ON CONFLICT) / S-01~09 / N-01~03. **round 3 Codex 재감사 fix 7건**: R2-C01/C02 (untargeted on_conflict + pre-lookup partial index infer 회피) / R2-S01 (enqueue 실패 cleanup) / R2-S02 (retry_count++ + 터미널 필드 초기화) / R2-S03 (response.json ValueError → ProviderError wrap) / R2-S04 (DocumentTopic DO UPDATE greatest confidence) / R2-S06 (`_insert_document_idempotent` (id, is_new) 튜플) / R2-N01 (hash_prompt_search 가 SYSTEM_PROMPT_TEMPLATE 본문 hash 자동 포함). **round 3 후속 시연 발견 fix 4건** (C-36): R3-C01 (LLM_REQUEST_TIMEOUT 60→180s, 5곳 통일), R3-C02 (documents_service SELECT DISTINCT + ORDER BY sort_ts label fix), CSO 3.4.1 csv-quoted N-Triples parser 재작성 + 5 cluster seed 라벨 교체 (Operating Systems/Automata Theory/Interactive Computer Graphics/Multimedia Systems/Scientific Computing) + docker-compose 5432→5433. |
| 1 | **A5 clickbait** | 사용자 제공 DoRA 모듈 wrap | ✅ 외부 서비스 ([PR #2](https://github.com/nwejnkasdf/SKKU-insight/pull/2)) / **(v13 라운드)** backend 통합은 default 비활성 — 사용자 News 소스 활성화 시만 |
| 1 | **A6 interest-bayesian** | 행동 로그 API, atomic UPSERT, active day 기반 시간 감쇠, 1-hop propagation | ✅ **시연 검증 완료 (2026-05-17, docker compose 통합)** — alembic 0004 (6 신규 테이블 + 12 partial UNIQUE + system_config 2 row seed) + 9 endpoint (interest/state, events x2, feedback x4 + saved/hidden + delete) + decay daily cron (18 UTC = 03 KST) + onboarding bootstrap 협업 + Codex 2 라운드 감사 ([PR #18](https://github.com/nwejnkasdf/SKKU-insight/pull/18)). **3 PR-stack**: PR-1 contracts SOR (9890a17) + PR-2 alembic+ORM (faac655) + PR-3 본문 (4c3f8f1) + PR-3 tests (8ebeeca) + Codex 1차 fix 8건 (9e6242b) + Codex 2차 fix 4건 (5d2feec). **결정 매트릭스 17건**: Decay daily cron only / Redis dwell cap / 14-day boost daily 차감 + boost_applied_at_active_day 컬럼 / cluster + 1-hop child boost / propagation feature flag (env false default) / payload-hash 200/409 / not-interested 하이브리드 정렬 2 (Bayesian P1-4 분배 + NotInterestedTopic 최고 confidence 1건) / 207 Multi-Status / system_config A6 read-only + A10 UI 분담. **시연 검증**: signup → consent → /interest/state 빈 응답 + NFR-04 마스킹 → POST /events view 200 → idempotency 200(match)/409(mismatch) → /events/batch 207 (3 accepted + 1 duplicate) + C-03 batch race fix 정합 (user_event 4 row 정확 보존). |
| 2 | **A7 leaf-lifecycle + traversal** | LifecycleEvaluator + D 하이브리드 + TraversalEngine(extend/retract/split/archive/**merge**) + leaf 재배치 LLM + 3단계 강등 + propagation 토글 | ✅ **완료** (2026-05-17) — 7-commit PR-stack + Codex 3 라운드 23 fix |
| 2 | **A8 recommendation** | core/adjacent/discovery + fallback + Cold-start + first trace 생성 + emerging quota | ✅ **시연 검증 완료 (2026-05-17, 실 GPT-5.5 docker compose 통합)** — 7-commit PR-stack + Codex 3 라운드 audit fix. 10 cards + 5/3/2 slot + Korean reason 23~31자 + NFR-04 + cache hit + cold_start=true 모두 통과. P2-23 functional index PostgreSQL 16 검증 해소 |
| 3 | **A9 electron-client** | UI-01~05, safeStorage, 한국어 i18n, codegen된 api.ts 사용 | ⬜ |
| 3 | **A10 admin-console** | UI-06 Next.js 콘솔, codegen된 api.ts 사용 | ⬜ |
| 4 | **A11 test-ci** | pytest 통합, vitest, GitHub Actions, AT 자동화 (6 cross-check 는 A2 가 이미 작성) | ⬜ |
| 4 | **A12 demo-seed** | 5+ 페르소나 + 14일 active day 인터랙션 시뮬레이션 + LLM mock fixture 캡처 | ⬜ |

### 의존 그래프

```
A1 ──(독립, 완료)──
A2-stub ──> 모든 후속 (contracts.py + OpenAPI export)
A2 ──> A4, A5, A6, A9, A10
A3 ──> A4, A6, A7, A8
A4 ──> A5, A6, A7, A8
A5 ──> A4, A8
A6 ──> A7, A8
A7 ──> A8
A8 ──> A9
A2+A8 ──> A10
all ──> A11, A12
```

## 작업 시작 전 체크리스트 (per 에이전트)

- [ ] [`docs/decisions.md`](docs/decisions.md) 1회 통독.
- [ ] [`docs/decision-backlog.md`](docs/decision-backlog.md) 통독. P0이 자기 모듈 막는지 확인.
- [ ] [`docs/sdd/contracts.md`](docs/sdd/contracts.md) + [`docs/sdd/agent-orchestration.md`](docs/sdd/agent-orchestration.md) 통독.
- [ ] 자기 디렉토리의 모든 docs MD 통독.
- [ ] 의존 인접 모듈의 인터페이스(시그니처)만 발췌 통독.
- [ ] **contracts.py 외 enum·error code·Redis key 정의 없음을 확인**.
- [ ] 새 식별자(테이블·환경변수)는 docs와 충돌 없는지 확인.
- [ ] 작업 종료 시 새 미결 항목을 [`docs/decision-backlog.md`](docs/decision-backlog.md)에 추가.
- [ ] `mypy --strict`, `ruff`, `tsc --strict` 통과 확인.
- [ ] `scripts/check_*.py` 6종 모두 통과 (CI에서도 자동 검증).

## 1차 데모 시나리오 (작업 검증 기준)

1. 신규 가입 → 동의 → CSO 12 클러스터 중 3개 선택 → Cold-start 대시보드 10개 (5/3/2 슬롯).
2. 추천 카드 클릭·저장·숨김 → 관리자 콘솔에서 베이지안 사후·trace path 변화 관찰.
3. 다음 active day 시뮬레이션 (`make seed --advance-active-days 1`) → 새 emerging 리프 생성 + active 승격 + trace path extend.
4. 관리자 콘솔에서 수집 실패 1건 만든 뒤 재실행 → 성공.
5. 동의 철회 → 추천 중단 + 재동의/계정삭제 분기 표시.

## 시연 30분 전 최종 체크

```bash
# 1. CodexOAuth 사전 인증 (시연 default — `.env.example` LLM_PROVIDER=codex_oauth, 2026-05-18)
make codex-login                                    # 호스트에서 ChatGPT OAuth (브라우저)
                                                    # → ~/.codex/auth.json refresh_token 30일 유효
docker compose up -d && make codex-status           # 컨테이너 안 'Logged in using ChatGPT' 확인

# 2. DB 초기화 + 시드
docker compose down -v && docker compose up -d postgres redis
make migrate && make import-cso && make create-admin && make seed --full
docker compose up -d
make smoke-test       # 5+ 페르소나 dashboard 호출 + 응답 검증
cd client && npm start
```

> codex_oauth refresh 가 30일 안에 깨지거나 access 만료 시 → `codex logout && make codex-login`
> 또는 `.env` 의 `LLM_PROVIDER=openai` + `OPENAI_API_KEY` 로 fallback (시연 narrative 다르게).

자세히는 [`docs/sdd/agent-orchestration.md §9`](docs/sdd/agent-orchestration.md).

## 변경 절차

본 가이드를 수정해야 할 사유가 생기면:
1. `docs/decisions.md` 갱신.
2. 영향 받는 모듈 docs/ 갱신.
3. **본 AGENTS.md** 의 디렉토리 지도·작업 규칙·에이전트 분할표 동기화.
4. 영향 큰 변경은 [`docs/decision-backlog.md`](docs/decision-backlog.md)에 기록.
5. `docs/sdd/contracts.md` 변경 시 `backend/app/contracts.py` PR로 사용자 승인 받음.

마지막 갱신: 2026-05-19 (**P2 백로그 그룹 C 일부 — P2-27 archived_at_active_day + P2-28 candidate_pool_ids 카테고리별 분리 close** — C-44 라운드). 본 라운드 변경 (working tree, 미커밋): (1) **P2-27 close** — alembic 0008 으로 `user_cso_traversal.archived_at_active_day INTEGER NULL` + 인덱스 추가. `operations.execute_archive(active_day_counter)` 인자 + `execute_merge` 의 loser archive 시 동일 컬럼 저장. `queries.get_top_archived_trace` + `get_archived_traces_with_score` 가 `COALESCE(archived_at_active_day, last_activity_active_day)` fallback (backward-compat). default.py 의 `_resolve_active_day_counter` helper 가 User SELECT — protocol.py 시그니처 변경 회피. ArchivedTraceSummary 가 `tr.archived_at_active_day` 직접 참조 (NULL 인 0008 이전 row 만 last_activity fallback). (2) **P2-28 close (옵션 A2 카테고리별 분리)** — alembic 0008 으로 `user_profile.candidate_pool_ids JSONB NOT NULL DEFAULT '{}'`. `profile/schemas.py::CSOTopicCandidatePool` 신규 — fusion / deepening / broadening 3 카테고리. `_build_cso_candidate_pool` 반환 타입 변경 + 카테고리별 다른 풀 (fusion=active path + archive path + tail successors, deepening=active path + tail successors, broadening=archive path + tail predecessors). `generate_profile_payload` validation 강화 — 응답 각 카테고리 ID 가 자기 카테고리 풀 안에 있는지 확인 (이전: cso_graph 전체 멤버십만 — LLM hallucination 가능). `upsert_user_profile(candidate_pool=...)` 인자 + JSONB 영속화. prompt 도 `cso_candidate_pool.fusion` / `.deepening` / `.broadening` 명시. (3) **회귀 가드 14 신규** — `tests/profile/test_audit_regressions.py::TestP2_27_ArchivedAtActiveDay` 7 + `TestP2_28_CandidatePoolCategorized` 7. (4) **fixture 정합 갱신** — `test_prompt_builder.py` + `test_service_validation.py` 의 `_make_input()/_llm_input()` 가 CSOTopicCandidatePool 사용. **검증**: 6 cross-check (env code 131 / docs 132 / .env.example 131, api_docs 55=55, schema 25 tables, error_codes 45=45, redis_keys [OK], contracts [OK]) + `ruff All checks passed!` + `mypy --strict Success: no issues found in 151 source files` + pytest **71 profile + 25 topic 모두 green**. **백로그 변화**: P2 활성 19 → **17** (P2-27 + P2-28 close). 잔여 P2-26 (LLMProvider.complete output_schema) + P2-29 (recommendation_cache 버전) 는 후속 세션 — P2-26 은 5 provider 영향 (Anthropic 미사용 — codex_oauth/openai/openrouter/mock 만 본격 구현 결정), P2-29 는 multi-worker 진입 시. **다음 작업 후보**: A9 electron-client (UI-01~05 + safeStorage + 한국어 i18n + codegen api.ts), Codex R2 재감사 (A8-v2 + C-43/C-44 회귀), 5 persona × 실 GPT-5.5 fusion 카드 시연.

이전: 2026-05-19 (**P2 백로그 그룹 A+B 검토 — close 4건 + CSO 3.4 → 3.4.1 전환 + --reset 운영 가드** — C-43 라운드). 본 라운드 변경 (working tree, 미커밋): (1) **P2-21 close** — alembic 0005 (A7) + 0007 (A8-v2) 가 `ck_collection_job_type` 을 7-value 로 갱신 완료 (`daily_collect, leaf_lifecycle, merge_evaluation, summary_generation, interest_decay, trace_merge, daily_user_profile_generation`) 검증. (2) **P2-19 close + CSO 3.4 → 3.4.1 전환** — `docker-compose.yml` 에 `cso_cache: {}` named volume + api service `cso_cache:/app/.cache/cso` mount (재시작 시 ~26MB CSV 영속화). URL/캐시 파일명/docs/config default 모두 `CSO.3.4.1.csv` 로 동기 (5 파일 + 2 env + 3 docs + 2 prompts). Makefile `seed-cso-cache FILE=...` 타깃 신규 — 호스트 사용자 파일을 컨테이너 cso_cache volume 에 카피 (오프라인 시연 + KMI 서버 트래픽 절감). 사용자가 직접 제공한 `~/Downloads/CSO.3.4.1.csv` (26.9MB, 165913 라인) 가 1차 source. (3) **P2-16 + P2-10 close** — `reset_cso_tables(session, force_orphan=False)` 가드 추가. `dynamic_leaf_topic` 또는 `user_cso_traversal` 행 존재 시 default 거부 (RuntimeError + 카운트 메시지). `scripts/import_cso.py --force-orphan-cso-refs` CLI 플래그로 우회 노출. 회귀 가드 5 신규 (정적 시그니처 1 + 동적 mock 분기 3 + CLI 플래그 검증 1). (4) **P2-25 A11 시점 유지 결정** — conftest 가 모든 테스트 모듈 공유 의존성이라 부분 fix 위험 → A11 (test-ci) 머지 시 nested transaction (SAVEPOINT) 패턴으로 전반 정비 명시. 본 라운드 검토 노트만 추가, active 유지. **검증**: 6 cross-check 모두 통과 (check_env code 131 / docs 132 / .env.example 131, check_api_docs 55=55, check_schema 25 tables, check_error_codes 45=45, check_redis_keys [OK], check_contracts [OK]) + `ruff All checks passed!` + `mypy --strict Success: no issues found in 151 source files` + pytest `tests/topic/test_audit_regressions.py::test_audit_p2_16_p2_10_*` **5 passed in 1.11s**. **백로그 변화**: P2 28 → 활성 22 → 활성 19 (P2-10/16/19/21 close). 4 P2 backlog (A8-v2 P2-26/27/28/29) 는 A11 시점 P2-25 와 별개 운영 단계 작업. **다음 작업 후보**: 그룹 C (A8-v2 운영 fix P2-26~29), A9 electron-client, Codex R2 재감사, 5 persona 시연 데이터 시드.

이전: 2026-05-19 (**A8-v2 UserProfile + Discovery Fusion + Reincarnation Pivot — 본문 + Codex R1 audit + R1 fix + 머지** — C-42 라운드, decisions.md §15). [PR #25](https://github.com/nwejnkasdf/SKKU-insight/pull/25) (merge commit `63f2cdde`) 머지 완료. 단일 commit `71528db` (30+ files, +5600/-200 ~). discovery slot 2 의 본질을 "trust=high trend 정렬" → "Fusion 1 (archive x current cross-product) + Reincarnation 1 (`score_tail >= 0.6` archived trace 부활)" 로 pivot. SRS FR-41 "잠재적으로 관심 있을 수 있는" 의도 회복. core 5 + adjacent 3 안정성 base 유지. **7-PR-stack** (단일 commit 안 논리 그룹): PR-1 alembic 0007 + UserProfile ORM + ck_collection_job_type 7-value + PR-2 contracts (JobType.DAILY_USER_PROFILE_GENERATION + RedisKey 2 + ErrorCode 2) + Settings 7 신규 env + PR-3 `app/profile/` 5 파일 (~900줄, schemas/config_loader/prompt_builder/service + Pydantic strict + USER_PROFILE_JSON_SCHEMA) + PR-4 worker/jobs/user_profile.py + scheduler 7th cron entry + PR-5 recommendation (candidates fusion/reincarnation/trend 분리 + engine `_build_discovery_pools` per-source fallback chain + reasons 거부 키워드 강화) + traversal/queries 3 신규 (get_archived_traces_with_score / get_top_archived_trace / get_descendant_archived_leaves) + PR-6 docs (decisions §15 + decision-backlog C-42 + SRS 정합 박스 3개 [FR-41 / 04-data-model UserProfile / NFR-04] + algorithms/recommendation-ranking §Discovery 본문 갱신 + sdd/contracts + data/schema UserProfile § + api/recommendation A8-v2 cron ErrorCode 표 + ops/env-vars A8-v2 § + 7 env 행) + PR-7 account_deletion CASCADE + AGENTS.md + tests/profile/ 4 파일. **결정 매트릭스 21건** (사용자 결정 11 + 자체 결정 10): Discovery slot 본질 / 5:3:2 유지 / UserProfile ORM 만 (endpoint·UI 없음) / 6 필드 구조화 / score_tail >= 0.6 archive 만 input / daily 19 UTC / LLM_PROVIDER env 재사용 / reasoning high / cold-start cross-trace fusion / strict output schema / Lua atomic CAS / per-user try/except / cache-before-commit 회피 / Bridge CSO 매핑 가드 등. **학술 정합**: PersonaX (ACL '25) + LettinGo (KDD '25) + Guided Profile Generation + Serendipity 3-dim framework (RecSys-related '25, Fortuitous + Refreshing taste reincarnation + Enriching) + 본 라운드 고유 archive x current cross-product 융합 angle. **Codex R1 audit + R1 fix 7건** (Critical 2 + Suggested 7 + Nit 2 = 11 issue → R1 fix 7건 + P2 backlog 4건): Critical #1 lock TTL 180→360s (2x LLM timeout 마진) / Critical #2 fusion + reincarnation pool 분리 + slot 별 1개씩 강제 + per-source fallback chain (engine `_build_discovery_pools` 신규 + `_build_fusion_subslot` + `_build_reincarnation_subslot` + `_resolve_seed_id`) / Suggested #1 cache invalidate 별도 try/except (DB committed 후 redis 실패가 rollback 처리 X) / Suggested #3 Fusion bridge_id 가 active trace path 노드면 거부 / Suggested #4 fallback 이 doc rows 기반 판단 / Suggested #6 alembic downgrade `NotImplementedError` (CHECK violation 차단) / Nit #1 prompt 안 raw score 표현 → 자연어. **P2 backlog 4건 신규**: P2-26 LLMProvider.complete output_schema 인자 / P2-27 archived_at_active_day 별도 컬럼 / P2-28 fusion bridge_cso candidate_pool 매핑 강제 / P2-29 recommendation_cache key UserProfile.generated_at 버전 연결. **Anti-pattern 회피 9건**: cache-before-commit / read-then-write race / batch IntegrityError / Lock release race / Daily UNIQUE race (N/A) / NFR-04 score leakage / LLM provider 분기 가드 / LLM hallucination CSO 매핑 가드 / 토큰 폭주 cap. **테스트**: tests/profile/ 4 파일 (test_schemas / test_prompt_builder / test_service_validation / test_audit_regressions) — **57 passed** (52 → R1 audit_regressions 5 추가). ruff All checks (151 files) / mypy --strict (151 files) no issues / 6 cross-check 모두 통과 (check_schema 25 tables / check_contracts / check_env code 131 = .env.example 131 / check_error_codes 45 = 45 / check_redis_keys raw f-string 0 / check_api_docs 55 = 55). **WSL docker compose 통합 시연**: alembic 0001→0007 통과 (ck_collection_job_type 7-value 정상) + api lifespan 부트 (`cso_graph nodes=14707 edges=44131 clusters=12 / provider=codex_oauth / system_config_loaded=true`) + scheduler `JOB_REGISTRATIONS count=7` (A8-v2 신규 `user_profile_generation_job` 포함) + 모든 신규 모듈 surface (`_build_discovery_pools` / `_build_fusion_subslot` / `_build_reincarnation_subslot` / `_resolve_seed_id` / `generate_profile_payload` / `upsert_user_profile` / `get_user_profile` / `fetch_profile_llm_input` / `get_archived_traces_with_score` / `get_top_archived_trace` / `get_descendant_archived_leaves` / `UserProfile` ORM 10 컬럼 / `USER_PROFILE_JSON_SCHEMA` 6 required) import 검증 통과 + signup/login/consent/clusters HTTP 200 OK. **시연 narrative**: "AI 가 매일 사용자 archived trace 와 active trace 를 cross-product 해서 두 영역이 만나는 새 학습 path — Graph Algorithms (과거) x Memory Management (현재) = Memory-bounded Algorithms — 를 discovery 카드로 제시." **다음 작업** (P2 backlog 별도 세션): 5 persona × 실 GPT-5.5 fusion 카드 시연 (onboarding broad_interest ID 매핑 + 행동 데이터 SQL seed 필요), Codex R2 재감사 라운드 (R1 fix 회귀 + Suggested #2/#5/#7 + Nit #2 본격 fix), A9 electron-client (UI-01~05 + safeStorage + 한국어 i18n + codegen api.ts — A8 4 endpoint 안정 활용).

이전: 2026-05-18 (**CodexOAuth 본문 + reasoning_effort fix + 시연 default 전환** — C-41 라운드, decisions.md §14). 단일 commit `b3b89b8` (19 files, +1573/-43). 직전까지 stub 였던 `CodexOAuthProvider` 를 `codex exec --json --output-schema` subprocess wrap 으로 본문화 + 사용자 원래 결정 (reasoning_effort slot 별 분리, xhigh 미사용) 이 코드에 안 박혀 있던 dead intent fix + 시연 default `LLM_PROVIDER=codex_oauth` 권고 (`.env.example`, 사용자 본인 ChatGPT 구독 OAuth) + `service_tier=fast` + `--ignore-user-config` + `--ignore-rules` 모든 호출 적용. **방식 확정 근거** (직접 fetch): openclaw docs/concepts/oauth + developers.openai.com/codex/cli/{features, reference} + openai-python types/{chat/completion_create_params, shared_params/reasoning} + 사용자 PC `~/.codex/config.toml` 실측. **사용자 결정 8건** (decisions §14): subprocess wrap (PKCE X) / 공식 허용 path / 모델 모두 gpt-5.5 / reasoning_effort high·medium / 시연 default codex_oauth (.env.example 권고, Settings 코드 default 는 mock 유지 CI 안전) / web_search cached / service_tier fast / `--ignore-user-config`·`--ignore-rules` 항상. **자체 결정 8건**: sandbox read-only / workdir /tmp/codex-runtime / ~/.codex rw mount / lifespan binary fail-fast / --output-schema generic vs SEARCH_OUTPUT_SCHEMA / agent_message text 합침 / output+reasoning=completion / 글로벌 단일 토글. **infra**: backend/Dockerfile (Node 20 + `npm i -g @openai/codex` + sanity) + docker-compose api+worker `${HOME}/.codex:/root/.codex` rw mount + Makefile `make codex-login`·`make codex-status`. **통합 검증** (WSL docker compose): build api → reverent-dubinsky-1b706e-api 이미지 → container `codex --version` = `codex-cli 0.130.0` → `codex login status` = `Logged in using ChatGPT` → 실 codex exec (우리 argv 그대로, `--ignore-user-config` 효과로 input 24k→13k) → `"ping"`→`"pong"` 응답 + refresh 정상. **테스트**: test_codex_oauth.py 신규 15 케이스 + test_openai_reasoning_effort.py 신규 6 케이스 + test_lifespan_provider_guard.py 갱신 3 케이스 + test_openai_search.py 갱신 1 케이스 (32 passed, Redis 의존 4 error pre-existing). ruff All checks / mypy --strict 10 files no issues / 6 cross-check 모두 exit 0. **다음 작업**: A9 electron-client (UI-01~05 + safeStorage + 한국어 i18n + `client/src/generated/api.ts` codegen).

이전: 2026-05-17 (**Phase 2 A8 recommendation engine 완전 완료** — 9 commit PR-stack + Codex 3 라운드 audit fix + 실 GPT-5.5 통합 시연 검증). [PR #23](https://github.com/nwejnkasdf/SKKU-insight/pull/23). 9 commit-stack: c82ce83 PR-1 alembic 0006 + 3 ORM (Recommendation / RecommendationSlot / DocumentSummaryCache + daily UNIQUE + 3 CHECK) + 6476037 PR-2 본문 (`app/recommendation/` 11 파일 + `worker/jobs/cold_start.py` 본문 + `interest/service.py` trace creation hook + Settings 9 신규 + `recommendation.toml` + env-vars drift, ~3514 line) + ce35d8e PR-3 tests (9 파일 + conftest, 1852 line) + 15883d1 PR-4 R1 self-review fix (TopicChip dedup) + ebdd11f docs drift fix R1 + 099f837 PR-5 R2 Codex 재감사 fix (Critical #2 UTC 경계 fallback + Suggested #1 Lua atomic INCR+EXPIRE + P2-22/23/24 backlog) + ee627a2 PR-6 R3 통합 시연 fix (GPT-5 temperature/max_tokens 분기, openai.py) + c2c8bc1 docs drift fix R2 + db28af2 PR-7 R3 시연 추가 fix (DocumentTopic 매핑 + asyncpg CAST type + P2-25 backlog). **결정 매트릭스 7건** (decisions.md §13, decision-backlog C-40): PR-stack 7-commit 패턴 / Summary 캐시 = 신규 테이블 DocumentSummaryCache (사용자 결정 #2) / 첫 trace 생성 hook = cold_start orchestrator + ingest_event_atomic click hook 협업 (#3, A7 #6 plan TBD 완성) / PUT /onboarding/interests FR-55 = A8 범위 외 stub 유지 (#4, A9/A10 분담) / sentinel `cold_start_pseudo` + content_type='pseudo_cold_start' 활성화 / emerging quota = core 5 중 1 부재 시 active 회수 / score 컬럼 = Recommendation DB 영속 (admin 노출), 일반 사용자 응답 schema 부재 (NFR-04). **§11 anti-pattern 5건 사전 방어**: cache-before-commit / daily UNIQUE race / emerging quota race / NFR-04 score 노출 / lock token race. **Codex 3 라운드 audit 결과**: R1 self-review 1 fix (TopicChip dedup) + R2 Codex 외부 감사 Critical 2 / Suggested 1 / Discussion 2 / Acknowledged 6 — Critical #2 + Suggested #1 즉시 fix / P2-22/24/25 backlog / P2-23 R3 검증 해소 + R3 시연 발견 결함 3건 (GPT-5 temperature 분기 / cold_start DocumentTopic 매핑 / asyncpg CAST type) fix. **R3 시연 검증** (docker compose + 실 OpenAI chat/completions GPT-5.5, HTTP 200 61s): alembic 0001→0006 통과 → signup → consent → onboarding (AI/Systems/Theory 3 cluster) → cold_start_job (worker async) → dashboard 10 cards (core 5: Learning to Reason with LLMs / Transformers are SSMs / Llama 3.1 / NVIDIA Blackwell / IMO AI silver / adjacent 3: Model Context Protocol / Apple Intelligence / AI Scientist / discovery 2: AlphaFold 3 / Willow quantum chip) + Korean reason 23~31자 + NFR-04 응답 'score' 키 0건 + 2회차 cache hit + cold_start=true + sentinel `cold_start_pseudo` 활용 + click event → UserCSOTraversal trace 자동 생성 (A7 #6 plan TBD 완성) + GET /documents/{id}/summary cache miss 12.5s LLM → hit 31ms. pytest 28 passed (실 로직 90%) + 19 errors (conftest fixture, A11 P2-25 backlog). **다음 작업**: A9 electron-client (UI-01~05 + safeStorage + 한국어 i18n + `client/src/generated/api.ts` codegen — A8 4 endpoint 안정 활용).

이전: 2026-05-17 (**Phase 2 A7 leaf-lifecycle + traversal 완료** — trace operation 4 → 5 (merge 신규) + D 하이브리드 + Strict 검증 + INTEREST_PROPAGATION_ENABLED=true 토글). [PR #21](https://github.com/nwejnkasdf/SKKU-insight/pull/21) (merge commit `11f5aa3`) 머지. **9 commit-stack**: PR-1 contracts SOR (7332b34, JobType.TRACE_MERGE + ErrorCode 5 + RedisKey 3 + Settings 33) + PR-2 alembic 0005 (9f24ac3, UserCSOTraversal.merged_into_trace_id + ck_collection_job_type 갱신, A6 P2-21 해소) + PR-3 본문 (65f415f, app/traversal 5 + app/leaf_lifecycle 6 + worker/jobs 3 신규 + scheduler 3 cron, 3167 line) + PR-3 tests (37b686f, 47 unit test, 773 line) + PR-4 Codex R1 fix 10건 (485da9b, Critical 3 + Suggested 6 + Nit 1) + PR-5 Codex R2 재감사 fix 6건 (f8457b9, Critical 3 + Suggested 2 + R1 deferred Suggested 5 처리) + docs drift fix R1 (cef607d, cso-topic-traversal §3 + AGENTS A7 ✅) + PR-6 Codex R3 재재감사 fix 7건 (dbaefe6, Critical 3 + Suggested 4 — R3-NEW-S1 P2 등재) + docs drift fix R2 (d019d03, 인덱스/메타 8 파일 정합). **결정 매트릭스 23건** (decisions.md §12, decision-backlog C-39): PR-stack 9-commit / Codex 3 라운드 (R1 본문 + R2 재감사 + R3 재재감사, 모두 GPT-5.5 xhigh) / 실 GPT-5.5 시연 / propagation 토글 default true / 하이브리드 강등 (1단계 ingest 즉시 / 2-3단계 daily cron) / LLM Cap 폐지 (시연 단계) / alembic 0005 / Strict 검증 (confidence ≥0.6 + supporting ≥3 + anchor + label dedup 0.75) / trace_anchor retry cap=1 / merged leaf 추천 제외 / **trace merge operation 신규 도입** / input D union (A4 collection ∪ UserEvent click/save 24h) / split T 단축 + T'=분기점+B / merge winner=max activity + tie 시 trace_id 작은 쪽 / daily 18 UTC trace merge LLM cron. **Codex 23건 fix** (R1 10 + R2 6 + R3 7): A6 anti-pattern 회피 (C-01 read-then-write / C-02 cache-before-commit / C-03 batch race / S-03 alpha floor / S-04 lifespan race) + R1 신규 (split child_A 누락은 R2 에서 발견 후 fix / lock key 통일 / path 길이 1 archive count / Lua atomic CAS / composite PK 충돌 / Strict anchor 1-hop / daily archive_threshold = stale+archive_after / status filter + RETURNING / recommendation_cache invalidate 4 worker / docs §12 SOR / scheduler docstring / mark_stale_if_idle hook) + R3 추가 (mark_stale_if_idle lock 추가 / LLM semaphore TTL 동적 max(60, timeout+30) / OpenAI provider HTTP·JSON 오류 ProviderError wrap / daily_lifecycle TTL=max(traversal, trace_merge) / Lua release token / execute_archive atomic RETURNING / split SOR 표현 정합).

이전: 2026-05-17 (**Phase 1 A6 interest-bayesian 완료** — 행동 로그 + Beta-Bernoulli + active day decay). [PR #18](https://github.com/nwejnkasdf/SKKU-insight/pull/18) (merge commit `a0a3fbf`) 머지. 6 commit-stack: PR-1 contracts SOR (JobType.INTEREST_DECAY + ErrorCode 2 + RedisKey 3 + Settings 6) + PR-2 alembic 0004 (UserEvent/UserInterestState/SavedDocument/HiddenDocument/NotInterestedTopic/SystemConfig 6 신규 + 12 partial UNIQUE + system_config 2 row seed) + PR-3 본문 (`app/interest/{bucket,config_loader,decay,idempotency,propagation,router,schemas,service,topic_distribution}.py` + `app/events/{buffer,active_day}.py` + 9 endpoint + onboarding bootstrap 협업 + lifespan EventBuffer task) + PR-3 tests (12 신규). **Codex 2 라운드 독립 감사 12건 fix** (decision-backlog C-37/C-38): round 1 Critical 2 (atomic UPSERT lost update + idempotency cache-before-commit) + Suggested 5 (IntegrityError race + Lua dwell cap + GREATEST alpha floor + EventBuffer stop race + system_config fail-fast + onboarding savepoint) + round 2 재감사 Critical 1 (batch race regression — round 1 S-01 의 db.rollback() 가 앞선 entry row 소실) + Suggested 2 (IntegrityError 오분류 + test redis fixture 누락) + Nit 1 (boost_expired metric 과대). **통합 시연 검증** (docker compose 격리 환경): alembic 0001→0004 적용 + system_config_loaded=true + signup/consent/JWT 흐름 + /interest/state 200 + NFR-04 마스킹 + POST /events view + idempotency 200(match)/409(mismatch) + /events/batch 207 (3 accepted + 1 duplicate) + C-03 batch race fix 정합 (user_event 4 row 정확 보존). **결정 매트릭스 17건**: Decay daily cron only (18 UTC = 03 KST) / Redis dwell cap (Lua atomic INCR+EXPIRE) / 14-day boost 만료 daily 차감 + boost_applied_at_active_day 컬럼 / cluster + 1-hop child boost / propagation feature flag env (default false, A7 도입 후 true) / payload-hash idempotency / not-interested 하이브리드 (Bayesian P1-4 분배 + NotInterestedTopic 최고 confidence 1건) / system_config A6 read-only + A10 admin UI 분담 / 207 Multi-Status batch / 9 endpoint + 6 ORM 모델. **다음 작업**: A7 leaf-lifecycle + traversal (D 하이브리드 + extend/retract/split/archive + 3단계 강등 + INTEREST_PROPAGATION_ENABLED=true 토글).

이전: 2026-05-17 (**Phase 1 A4 collection 완료** — v13 Topic-driven Pivot 단일 구현물). [PR #16](https://github.com/nwejnkasdf/SKKU-insight/pull/16) 머지. alembic 0003 + `LLMProvider.search_with_tools()` (GPT-5.5 + OpenAI Responses API + web_search) + 3 라운드 Codex 감사 26건 fix (C-34/C-35/C-36) + 실 OpenAI GPT-5.5 통합 시연 검증 (26 documents inserted, NFR-25 self-summary 100% 준수, dedup cross-leaf 정상).

이전: 2026-05-11 (**v13 라운드 — A4 Topic-driven Pivot docs**). A4 collection 본문 구현 직전 사용자 토의에서 합의된 fundamental design pivot 을 docs SOR 에 박음 — 6 source 어댑터(arxiv/openalex/s2/dblp/RSS/네이버BS4) 폐기 후 `LLMProvider.search_with_tools()` 단일 경로로 전환. 사용자 원안("에이전트 기반 추천 시스템의 하네스 + 토픽이 먼저고 문서가 나중") 회복. P1-6 / P2-3 / P2-4 무효 마킹, C-33 (pivot) 신규. SRS FR-22~25 식별자 보존 + v13 해석 박스.

이전: 2026-05-11 (Phase 0b A3 cso-topic 완료 — 5 PR-stack. CSO 3.4 임포트 + NetworkX 그래프 + 7 endpoint 본문 + 11 ORM 모델 + alembic 0002. 3 라운드 독립 감사. `ruff` · `mypy --strict 100 files` · `pytest 65/65` · 6 cross-check 통과).
