# SKKU InSight — Agents Guide

본 파일은 **모든 후속 에이전트(Claude / Codex / 사람 협업자)**가 본 저장소에서 작업을 시작할 때 가장 먼저 읽는 진입점이다. 모델·도구 종속 표현은 회피하므로 Claude·Codex·사람이 동일하게 해석할 수 있다.

> **사람이 읽는 프로젝트 소개**는 [`README.md`](README.md). 본 파일은 에이전트(코드 작성자) 전용 운영 헌법이다.

## 프로젝트 한 단락

`SKKU InSight`는 이공계 학생·연구자·교수가 직접 검색하지 않아도 자기 관심에 맞는 CS/AI 기술 동향을 선제적으로 받아볼 수 있는 **Windows 데스크톱 애플리케이션**이다. **사용자별 CSO 그래프 traversal trace**가 관심 상태의 단위이고, 그 위에 **사용자별 dynamic leaf 토픽**이 분기되며, 추천은 **current/adjacent/proactive 3 카테고리 ↔ core/adjacent/discovery 슬롯 1:1 매핑**이다. 백엔드 FastAPI + PostgreSQL/Redis, 클라이언트 Electron+React+TS, 관리자 Next.js, 모두 단일 `docker-compose.yml`로 기동. 성균관대 소프트웨어공학개론 조별과제 산출물이며 1차 목표는 **풀스택 동작 데모**(10-20명 동시 사용자 가정).

## 첫 30분 — 4개 진입 문서

새 에이전트는 다음 4개를 순서대로 읽으면 작업 시작 가능.

1. **[`docs/decisions.md`](docs/decisions.md)** — 12+ 라운드 결정 매트릭스 압축본. 모든 코드 결정의 단일 진실 공급원. SRS와 충돌 시 본 파일이 우선 (단 SRS의 FR/NFR/AT 식별자·표는 보존).
2. **[`docs/decision-backlog.md`](docs/decision-backlog.md)** — P0/P1/P2 백로그. **P0 1건(DoRA 모듈 경로) 외 모든 항목에 default·stub 경로가 정의돼 있다**.
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
├── docs/                              # 산출 문서 54+ 파일
│   ├── README.md                      # docs 인덱스
│   ├── decisions.md                   # 결정 매트릭스 (SOR)
│   ├── decision-backlog.md            # P0/P1/P2
│   ├── srs/                           # SRS 분할본 10개
│   ├── sdd/                           # 설계: 아키텍처·데이터 흐름·배포·모듈 경계·기술 스택
│   │   ├── architecture.md
│   │   ├── data-flow.md
│   │   ├── deployment.md
│   │   ├── module-boundaries.md
│   │   ├── tech-stack.md
│   │   ├── concurrency.md             # 동시성 가드 (10-20명)
│   │   ├── api-conventions.md         # HTTP 통신 규약 + OpenAPI codegen
│   │   ├── contracts.md               # contracts.py SOR 명세
│   │   └── agent-orchestration.md     # 멀티 에이전트 운영 헌법
│   ├── api/                           # FastAPI 엔드포인트 명세 8개
│   │   └── (auth/consent/onboarding/topics/interest/collection/recommendation/admin)
│   ├── algorithms/                    # 7개
│   │   └── (interest-bayesian/cso-topic-traversal/leaf-topic-lifecycle/recommendation-ranking/cold-start/clickbait-integration/cso-mapping)
│   ├── data/                          # 5개 (schema/erd/sources-registry/cso-import/seed-personas)
│   ├── ops/                           # 5개 (docker-compose/env-vars/ci-cd/admin-bootstrap/runbooks)
│   ├── security/                      # 5개 (auth-flow/token-handling/rate-limiting/password-policy/threat-model)
│   └── ux/                            # 4개 (wireframes/ui-states/i18n/client-behaviors)
└── (코드 디렉토리는 후속 에이전트가 만든다)
    backend/  client/  admin-console/  workers/  llm-adapter/
    services/clickbait-detector/  scripts/  .github/
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
| 낚시성 | DoRA 파인튜닝 `A.x 4.0 light` 모듈 wrap (사용자 보유, P0-1 대기) |
| 임베딩 | **미사용**. 토픽 유사도는 CSO 그래프 거리, 중복 제거는 URL/DOI/제목 정규화 + Levenshtein |
| 수집 소스 | 학술 4종 (arXiv/OpenAlex/Semantic Scholar/DBLP) + 빅테크 RSS 30+ + 뉴스 (네이버 BS4 / TC / Verge / Wired / MIT TR / IEEE Spectrum) + sentinel `cold_start_pseudo` |
| 시드 | 5+ 페르소나 + 14일치 인터랙션 (active day 기반) |
| 동시성 | 10-20명 가정. single-flight + user-mutex + atomic SQL + LLM semaphore + batch flush + consent cache + jitter |

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

| Phase | ID | 산출 | 1순위 참조 |
|---|---|---|---|
| 0a (게이트) | **A1 docs-bootstrap** | 본 `docs/` (완료) | — |
| 0a (게이트) | **A2-stub** | `backend/app/contracts.py`, 모든 router endpoint signature(`raise NotImplementedError`), Pydantic schemas, `scripts/export_openapi.py` → 사용자 검수 + codegen | `decisions.md`, `sdd/contracts.md`, `sdd/api-conventions.md`, `sdd/agent-orchestration.md`, `api/*` 8개 |
| 0b | **A2 backend-foundation** | A2-stub 본문 채움. docker-compose, Alembic, 인증·동의·사용자·**onboarding**, 보안, DB pool 분리, sentinel Source 시드 | `decisions.md`, `sdd/tech-stack.md`, `sdd/module-boundaries.md`, `sdd/concurrency.md`, `data/schema.md`, `api/auth.md`, `api/consent.md`, `api/onboarding.md`, `security/*`, `ops/docker-compose.md`, `ops/env-vars.md` |
| 0b | **A3 cso-topic** | CSO 임포트, NetworkX 캐시, Topic·CSOTopic, 그래프 탐색 API | `algorithms/cso-mapping.md`, `data/cso-import.md`, `data/schema.md`, `api/topics.md` |
| 1 | **A4 collection** | 소스 어댑터(arXiv/OpenAlex/S2/DBLP/RSS/네이버 BS4), CollectionJob, jitter, dedup | `data/sources-registry.md`, `data/schema.md`, `api/collection.md`, `sdd/data-flow.md` |
| 1 | **A5 clickbait** | 사용자 제공 DoRA 모듈 wrap (P0-1 해결 후) | `algorithms/clickbait-integration.md`, `data/schema.md`(ClickbaitResult), `ops/env-vars.md` |
| 1 | **A6 interest-bayesian** | 행동 로그 API, atomic UPSERT, **active day 기반 시간 감쇠**, 1-hop propagation | `algorithms/interest-bayesian.md`, `algorithms/cso-topic-traversal.md`, `data/schema.md`, `api/interest.md` |
| 2 | **A7 leaf-lifecycle + traversal** | LifecycleEvaluator + D 하이브리드 + **TraversalEngine**(extend/retract/split/archive) + leaf 재배치 LLM + 3단계 강등 | `algorithms/cso-topic-traversal.md`, `algorithms/leaf-topic-lifecycle.md`, `sdd/module-boundaries.md` |
| 2 | **A8 recommendation** | core/adjacent/discovery + fallback + Cold-start (current/adjacent/proactive 1:1) + first trace 생성 + emerging quota | `algorithms/recommendation-ranking.md`, `algorithms/cold-start.md`, `algorithms/cso-topic-traversal.md`, `api/recommendation.md`, `sdd/concurrency.md` |
| 3 | **A9 electron-client** | UI-01~05, safeStorage, 한국어 i18n, Page Visibility dwell_tick, 비동기 cold-start 폴링. **codegen된 api.ts만 사용** | `ux/wireframes.md`, `ux/ui-states.md`, `ux/i18n.md`, `ux/client-behaviors.md`, `api/*` 8개, `security/auth-flow.md` |
| 3 | **A10 admin-console** | UI-06 Next.js 콘솔. **codegen된 api.ts만 사용** | `ux/wireframes.md`(UI-06), `api/admin.md`, `ops/admin-bootstrap.md` |
| 4 | **A11 test-ci** | pytest, vitest, GitHub Actions, AT 자동화, contracts.yml 6종 cross-check | `ops/ci-cd.md`, `sdd/agent-orchestration.md §5`, `srs/08-acceptance-tests.md` |
| 4 | **A12 demo-seed** | 5+ 페르소나 + 14일 active day 인터랙션 시뮬레이션 + LLM mock fixture 캡처 | `data/seed-personas.md`, `data/schema.md`, `decisions.md` §9 |

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

마지막 갱신: 2026-05-09.
