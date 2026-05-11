# SKKU InSight — Agents Guide

본 파일은 **모든 후속 에이전트(Claude / Codex / 사람 협업자)**가 본 저장소에서 작업을 시작할 때 가장 먼저 읽는 진입점이다. 모델·도구 종속 표현은 회피하므로 Claude·Codex·사람이 동일하게 해석할 수 있다.

> **사람이 읽는 프로젝트 소개**는 [`README.md`](README.md). 본 파일은 에이전트(코드 작성자) 전용 운영 헌법이다.

## 프로젝트 한 단락

`SKKU InSight`는 이공계 학생·연구자·교수가 직접 검색하지 않아도 자기 관심에 맞는 CS/AI 기술 동향을 선제적으로 받아볼 수 있는 **Windows 데스크톱 애플리케이션**이다. **사용자별 CSO 그래프 traversal trace**가 관심 상태의 단위이고, 그 위에 **사용자별 dynamic leaf 토픽**이 분기되며, 추천은 **current/adjacent/proactive 3 카테고리 ↔ core/adjacent/discovery 슬롯 1:1 매핑**이다. 백엔드 FastAPI + PostgreSQL/Redis, 클라이언트 Electron+React+TS, 관리자 Next.js, 모두 단일 `docker-compose.yml`로 기동. 성균관대 소프트웨어공학개론 조별과제 산출물이며 1차 목표는 **풀스택 동작 데모**(10-20명 동시 사용자 가정).

## 첫 30분 — 4개 진입 문서

새 에이전트는 다음 4개를 순서대로 읽으면 작업 시작 가능.

1. **[`docs/decisions.md`](docs/decisions.md)** — 12+ 라운드 결정 매트릭스 압축본. 모든 코드 결정의 단일 진실 공급원. SRS와 충돌 시 본 파일이 우선 (단 SRS의 FR/NFR/AT 식별자·표는 보존).
2. **[`docs/decision-backlog.md`](docs/decision-backlog.md)** — P0/P1/P2 + C-급 백로그. **P0 0건. P1 활성 7건 + P2 활성 5건은 default·stub 경로 정의됨. C-급 32건 모두 해소** (A2 자체 검수 + Codex review v1·v2·v3 + multi-worker + 옵션 B + mypy strict 26 + 초기 결정 11, 2026-05-11).
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
├── .env.example                      # ✅ A2 + A3 (CSO_DOWNLOAD_URL): env-vars.md 골격 + NAVER_CLEANUP_CRON + UVICORN_WORKERS + CSO_DOWNLOAD_URL
├── docs/                              # 산출 문서 54+ 파일
│   ├── README.md                      # docs 인덱스
│   ├── decisions.md                   # 결정 매트릭스 (SOR)
│   ├── decision-backlog.md            # P0/P1/P2 + C-급 (A2 후 32 해소 / A3 자체+Codex 감사 11 신규 backlog)
│   ├── srs/                           # SRS 분할본 10개
│   ├── sdd/                           # 9: 아키텍처·데이터 흐름·배포·모듈 경계·기술 스택·동시성·API 규약·계약·에이전트 오케스트레이션
│   ├── api/        (8)                # auth/consent/onboarding/topics/interest/collection/recommendation/admin
│   ├── algorithms/ (7)                # interest-bayesian/cso-topic-traversal/leaf-topic-lifecycle/recommendation-ranking/cold-start/clickbait-integration/cso-mapping
│   ├── data/       (5)                # schema/erd/sources-registry/cso-import/seed-personas — A3: cso-import idempotency · schema CSOTopicParent §
│   ├── ops/        (5)                # docker-compose/env-vars/ci-cd/admin-bootstrap/runbooks — A3: env-vars CSO_DOWNLOAD_URL
│   ├── security/   (5)                # auth-flow/token-handling/rate-limiting/password-policy/threat-model
│   └── ux/         (4)                # wireframes/ui-states/i18n/client-behaviors
├── backend/                          # ✅ A2 본문 + A3 cso-topic 완료
│   ├── Dockerfile                    # python:3.12-slim
│   ├── pyproject.toml                # FastAPI + Pydantic + SQLAlchemy async + bcrypt + rq + rq-scheduler + slowapi + structlog + httpx + networkx (A3 mypy override 추가)
│   ├── .env.example                  # backend 단독 부트용 (compose 미사용 시)
│   ├── alembic.ini + alembic/env.py + versions/{0001_initial_a2_tables.py, 0002_a3_cso_traversal_leaf_tables.py}
│   ├── app/
│   │   ├── main.py + lifespan.py + redis.py + contracts.py
│   │   ├── config/                                       # ✅ A3 패키지화: __init__.py (BaseSettings + CSO_DOWNLOAD_URL) + broad_interests.toml (12 entry 시드 SOR)
│   │   ├── db/{base, engine, session, models/*}          # ✅ 11 모델 — A2 8 (User/AdminUser/UserConsent/UserCSOTraversal/BroadInterest/CSOTopic/Source/SourcePolicy) + A3 3 (CSOTopicParent/DynamicLeafTopic/DynamicLeafTopicCSOTopic)
│   │   ├── security/{password, jwt, rate_limit, consent_cache, idempotency, deps}  # + common_passwords.txt
│   │   ├── auth/, consent/, onboarding/, admin/          # 17 endpoint 본문 (service + router)
│   │   ├── topic/                                        # ✅ A3 본문 완료 — graph/mapping/cso_importer/cache/lifespan/cso_service/leaf_service/trace_service/router/schemas (9 파일) — 7 endpoint 본문 + /documents NotImplementedError (A4·A8 의존)
│   │   ├── interest/, collection/, recommendation/       # stub 유지 (A6/A4/A8)
│   │   ├── middleware/{request_id, jwt_auth, consent_gate, exception_handler, structlog_mask}
│   │   ├── llm_provider/{protocol, mock, openai, anthropic, openrouter, codex_oauth, _concurrency}  # Redis 분산 semaphore
│   │   ├── worker.py + scheduler.py + worker/jobs/{account_deletion(완료), cold_start/naver_cleanup/collection/interest_decay/merge_evaluation(stub)}
│   ├── scripts/{create_admin, reset_password, export_openapi, import_cso}   # ✅ A3: import_cso CLI (--reset/--refresh/--dry-run, 단일 transaction)
│   └── tests/{conftest + security/admin/llm_provider unit + auth/consent integration + topic/{test_mapping, test_graph, test_importer, test_audit_regressions, fixtures/small_cso.csv} + fixtures/mock_llm/}    # ✅ A3: 65 tests (45 + 12 자체감사 + 6 Codex 1st + 2 Codex 2nd)
├── scripts/                          # ✅ A2: 6 cross-check (api_docs / schema / env / error_codes / redis_keys / contracts) + _common.py
├── prompts/                          # 에이전트별 kickoff prompts (A1 ✅ / A2-stub ✅ / A2 ✅ / A3 ✅ / 나머지 ⬜)
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
| LLM 어댑터 | **`MockProvider` (default)** + OpenAI/Anthropic/OpenRouter/CodexOAuth(local experimental). 환경변수 `LLM_PROVIDER` 토글 |
| 낚시성 | DoRA 파인튜닝 `A.X-4.0-Light` 모듈 (✅ `clickbait_module/` 자체 vLLM 서비스, P0-1 해결 2026-05-11) |
| 임베딩 | **미사용**. 토픽 유사도는 CSO 그래프 거리, 중복 제거는 URL/DOI/제목 정규화 + Levenshtein |
| 수집 소스 | 학술 4종 (arXiv/OpenAlex/Semantic Scholar/DBLP) + 빅테크 RSS 30+ + 뉴스 (네이버 BS4 / TC / Verge / Wired / MIT TR / IEEE Spectrum) + sentinel `cold_start_pseudo` |
| 시드 | 5+ 페르소나 + 14일치 인터랙션 (active day 기반) |
| 동시성 | 10-20명 가정. single-flight + user-mutex + atomic SQL + **LLM Redis 분산 semaphore** (multi-worker 안전, C-19) + batch flush + consent cache + jitter |
| Worker 정책 | `UVICORN_WORKERS=1` default. N>1 시 DB pool 합산 = N × `PG_API_POOL_MAX` + `PG_WORKER_POOL_MAX` ≤ PostgreSQL `max_connections` (C-20) |
| Refresh rotation | Redis Lua `_LUA_VERIFY_ROTATE_ISSUE` (verify + mark `:rotated` + new meta INSERT + new index SET 단일 atomic, C-21). HMAC `:rotated` 마커로 family revoke |

## 작업 규칙 (모든 에이전트 공통, 14조)

1. **본문 한국어, 코드/CLI/식별자 영어**. 변수·함수·테이블 snake_case.
2. **FR-XX·NFR-XX·AT-XX·UC-XX는 SRS 표기 그대로**. 새 식별자 만들지 말 것.
3. **결정은 [`docs/decisions.md`](docs/decisions.md) 우선**. SRS와 충돌 시 그쪽 우선이지만 SRS 식별자·표는 보존.
4. **모델 종속 회피**. `MockProvider` (default)와 정식 API provider 모두에서 동일 동작. CodexOAuth는 local experimental.
5. **이미지 자산 부재**. SRS 분할의 `assets/*.png` 링크는 IEEE 830 원형 보존 목적의 죽은 링크. 와이어프레임 SOR은 [`docs/ux/wireframes.md`](docs/ux/wireframes.md), ERD는 [`docs/data/erd.mmd`](docs/data/erd.mmd).
6. **새 기능 임의 추가 금지**. SRS·본 가이드에 없으면 [`docs/decision-backlog.md`](docs/decision-backlog.md) P2로 추가 후 사용자 승인.
7. **TODO 마커**: `<!-- TODO: ... -->` 표기 + 동시에 `decision-backlog.md` 항목 추가.
8. **테스트**: pytest(backend) + vitest(client/admin) + GitHub Actions. AT-01~15 자동화 가능 항목은 [`docs/srs/08-acceptance-tests.md`](docs/srs/08-acceptance-tests.md) 표.
9. **시연 모드 default**: `LLM_PROVIDER=mock` 으로 부트, 외부 키 없이 핵심 흐름 동작.
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
| 1 | **A4 collection** | 소스 어댑터(arXiv/OpenAlex/S2/DBLP/RSS/네이버 BS4), CollectionJob, jitter, dedup | ⬜ |
| 1 | **A5 clickbait** | 사용자 제공 DoRA 모듈 wrap | ✅ 외부 서비스 ([PR #2](https://github.com/nwejnkasdf/SKKU-insight/pull/2)) / 🟡 backend 통합 대기 |
| 1 | **A6 interest-bayesian** | 행동 로그 API, atomic UPSERT, active day 기반 시간 감쇠, 1-hop propagation | ⬜ |
| 2 | **A7 leaf-lifecycle + traversal** | LifecycleEvaluator + D 하이브리드 + TraversalEngine(extend/retract/split/archive) + leaf 재배치 LLM + 3단계 강등 | ⬜ |
| 2 | **A8 recommendation** | core/adjacent/discovery + fallback + Cold-start + first trace 생성 + emerging quota | ⬜ |
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
docker compose down -v && docker compose up -d postgres redis
make migrate && make import-cso && make create-admin && make seed --full
docker compose up -d
make smoke-test       # 5+ 페르소나 dashboard 호출 + 응답 검증
cd client && npm start
```

자세히는 [`docs/sdd/agent-orchestration.md §9`](docs/sdd/agent-orchestration.md).

## 변경 절차

본 가이드를 수정해야 할 사유가 생기면:
1. `docs/decisions.md` 갱신.
2. 영향 받는 모듈 docs/ 갱신.
3. **본 AGENTS.md** 의 디렉토리 지도·작업 규칙·에이전트 분할표 동기화.
4. 영향 큰 변경은 [`docs/decision-backlog.md`](docs/decision-backlog.md)에 기록.
5. `docs/sdd/contracts.md` 변경 시 `backend/app/contracts.py` PR로 사용자 승인 받음.

마지막 갱신: 2026-05-11 (Phase 0b A3 cso-topic 완료 — 5 PR-stack. CSO 3.4 임포트 + NetworkX 그래프 + 7 endpoint 본문 + 11 ORM 모델 + alembic 0002. 3 라운드 독립 감사 (Opus 4.7 자체 + Codex GPT-5.5 1st·2nd) — Critical 6 fix + Suggested 9 fix + 11 신규 backlog. `ruff` · `mypy --strict 100 files` · `pytest 65/65` · 6 cross-check 통과. **다음 작업**: A4 collection 또는 A6 interest-bayesian.
