# SKKU InSight

> 이공계 학생·연구자·교수가 **검색하지 않아도** 자기 관심사에 맞는 CS/AI 기술 동향을 받아볼 수 있는 Windows 데스크톱 앱.
>
> 성균관대 소프트웨어공학개론 조별과제 산출물.

## 한 눈에

| 항목 | 내용 |
|---|---|
| 형태 | Electron(React + TypeScript) 데스크톱 앱 + FastAPI 백엔드 + Next.js 관리자 콘솔 |
| 인프라 | PostgreSQL 16 + Redis 7, 단일 `docker-compose.yml`로 기동 |
| 1차 목표 | 풀스택 동작 데모 (10-20명 동시 사용자) |
| 도메인 | CS/AI 기술 동향 (학술 논문 + 빅테크 공식 채널 + 테크 뉴스) |
| 산출물 | **54+ 문서 + 데모용 코드** (코드는 멀티 에이전트로 작성 예정) |

## 무엇을 만들고 있나

사용자가 매일 arXiv를 뒤지지 않아도 시스템이 사용자의 관심을 추론해 **하루 한 번 10개 추천 카드**로 보여줍니다. 클릭하고 저장하고 숨길수록 추천이 정교해집니다.

전형적인 사용자 흐름:

1. **가입 + 동의 + 12 클러스터(AI / Systems / Security / …) 중 N개 선택**
2. **Cold-start LLM**이 첫 10개 추천을 즉시 생성 (`core 5 / adjacent 3 / discovery 2` 슬롯)
3. 사용자가 카드를 보고 클릭·저장·숨김. 베이지안 관심도가 갱신됨
4. 다음 날 일일 수집(**LLM tool-use 검색 — GPT-5.5 가 user trace 의 active leaf 를 입력 받아 web_search 도구로 자료 fetch**) → 사용자별 trace에 맞춘 새 추천 10개
5. 시간이 지나면 **CSO 그래프 위 traversal trace**가 깊어지고 (예: AI → NLP → LLM), 그 끝에 사용자별 **dynamic leaf 토픽**이 분기 (예: "RAG 변형 기법", "Speculative Decoding")

## 핵심 디자인 포인트 (차별화)

- **Traversal trace = 관심 상태 객체**. 단일 노드가 아니라 사용자가 CSO 그래프 위를 흘러간 path 자체가 하나의 관심을 표현. 추천·요약·LLM 프롬프트 모두 trace 단위로 추론.
- **3 카테고리 ↔ 3 슬롯 1:1**. `current/adjacent/proactive` (모델) ↔ `core/adjacent/discovery` (추천) 가 자연스럽게 매핑.
- **Active day 회계**. 모든 시간 임계(라이프사이클·베이지안 감쇠)가 wallclock이 아니라 "사용자가 인터랙션한 날"의 단조증가 카운터 기반. 시험기간 잠수해도 trace가 깨지지 않음.
- **Beta-Bernoulli 베이지안 + 1-hop propagation**. atomic SQL UPSERT로 race condition 방어, leaf 활동이 부모 노드 점수로 propagate.
- **(v13 라운드)** 수집은 **LLM tool-use(web search) 단일 경로** — 6 source 어댑터 폐기. user trace 의 active leaf 를 LLM 에 입력 → LLM 이 자율 query 결정 + 웹 검색 + Document INSERT. NFR-25 정합은 prompt instruction (self-summary) 으로.
- **DoRA 파인튜닝 `A.X-4.0-Light` 낚시성 모듈** + LLM은 **CodexOAuthProvider 시연 default** (2026-05-18 본문 — `codex exec --json` subprocess wrap, 사용자 본인 ChatGPT 구독 OAuth, 비용 0) / OpenAI 정식 API / Mock fixture (CI) 토글. 모든 slot 모델 = `gpt-5.5`, slot 구분은 `reasoning_effort` (high/medium). clickbait_module 은 1차 시연 default 비활성.
- **10-20명 동시성 가드**: single-flight + user-level Redis mutex + atomic SQL + LLM semaphore + batch flush + consent cache.

## 진행 상황

| 단계 | 상태 |
|---|---|
| SRS v0.3 (IEEE 830) | ✅ 완료, 보존 |
| 결정 매트릭스 (12+ 라운드) | ✅ 완료 |
| 알고리즘 명세 7종 | ✅ 완료 |
| API 명세 8종 + 통신 규약 | ✅ 완료 |
| DB 스키마 + ERD | ✅ 완료 |
| 동시성 가드 + 멀티 에이전트 운영 헌법 | ✅ 완료 |
| **Phase 0a — A1 docs + A2-stub** | ✅ 완료 ([PR #4](https://github.com/nwejnkasdf/SKKU-insight/pull/4)) |
| **Phase 0b — A2 backend-foundation** | ✅ 완료 ([PR #7](https://github.com/nwejnkasdf/SKKU-insight/pull/7) — 17 endpoint 본문 + 35건 결함 해소) |
| **Phase 0b — A3 cso-topic** | ✅ 완료 (5 PR-stack — CSO 3.4 임포트 + NetworkX + 7 endpoint + 11 ORM + 3 라운드 감사 fix 15건) |
| **v13 라운드 — A4 Topic-driven Pivot** | ✅ docs 정합 (2026-05-11) + 코드 구현 + Codex 3-라운드 audit fix 26건 + 통합 시연 검증 ([PR #16](https://github.com/nwejnkasdf/SKKU-insight/pull/16), 2026-05-17). 6 source 어댑터 폐기 → LLM tool-use 검색 단일 경로 (GPT-5.5 + Responses API web_search). 26 documents inserted, NFR-25 self-summary 100% 준수. [`docs/decisions.md §10`](docs/decisions.md) |
| **Phase 1 — A6 interest-bayesian** | ✅ 완료 ([PR #18](https://github.com/nwejnkasdf/SKKU-insight/pull/18), 2026-05-17) — alembic 0004 (6 신규 테이블 + 12 partial UNIQUE + system_config 2 row seed) + 9 endpoint + decay daily cron + onboarding bootstrap 협업 + Codex 2 라운드 감사 12건 fix + 통합 시연 검증 (idempotency 200/409, batch 207, C-03 batch race fix 정합) |
| Phase 1 — A4 collection ✅ / A5 clickbait (외부 모듈 ✅, 1차 default 비활성) / A6 interest-bayesian ✅ | 🟢 Phase 1 완료 |
| **Phase 2 — A7 leaf-lifecycle + traversal** | ✅ 완료 (2026-05-17) — 7-commit PR-stack + Codex 3 라운드 23건 fix. trace operation 4 → 5 (merge 신규) + D 하이브리드 LifecycleEvaluator + Strict 검증 + INTEREST_PROPAGATION_ENABLED=true. alembic 0005 (UserCSOTraversal.merged_into_trace_id + ck_collection_job_type 갱신 P2-21 해소) + app/traversal 5 + app/leaf_lifecycle 6 + worker/jobs 3 신규 + 47 unit test |
| **Phase 2 — A8 recommendation** | ✅ 시연 검증 완료 (2026-05-17, 실 GPT-5.5 docker compose 통합) — 7-commit PR-stack + Codex 3 라운드 audit fix. alembic 0006 (Recommendation/RecommendationSlot/DocumentSummaryCache + daily UNIQUE) + `app/recommendation/` 11 파일 + `worker/jobs/cold_start.py` 본문 + `interest/service.py` 첫 trace 생성 hook + Settings 9 + `recommendation.toml`. **§11 anti-pattern 5건 사전 방어** + R1 self-review (TopicChip dedup) + R2 Codex 외부 감사 (Critical 2/Suggested 1/Discussion 2/Acknowledged 6 — Critical #2 UTC 경계 fallback + Suggested #1 Lua atomic 즉시 fix, P2-22/24 backlog, P2-23 R3 검증 해소) + R3 시연 발견 1 fix (GPT-5 temperature/max_tokens 분기). **R3 시연**: alembic 0001→0006 통과 → signup → consent → onboarding 3 cluster → cold_start_job (실 OpenAI GPT-5.5 HTTP 200 61s) → dashboard 10 cards (Learning to Reason with LLMs / Llama 3.1 / AlphaFold 3 등 실 논문·뉴스) + 5/3/2 slot + Korean reason 23~31자 + NFR-04 score 미노출 + 2회차 cache hit + cold_start=true |
| **CodexOAuth 라운드** | ✅ 완료 (2026-05-18, commit `b3b89b8`, C-41) — `CodexOAuthProvider` stub → 본문 (`codex exec --json --output-schema` subprocess wrap, OpenAI 공식 허용 path) + reasoning_effort dead intent fix (chat top-level + responses nested, high/medium slot 분리, xhigh 미사용) + 시연 default 전환 (`.env.example` 권고 = codex_oauth, 사용자 본인 ChatGPT 구독 OAuth 활용) + service_tier=fast + `--ignore-user-config`·`--ignore-rules` 모든 호출 + Dockerfile (`npm i -g @openai/codex`) + docker-compose ~/.codex rw mount + `make codex-login`/`make codex-status`. **통합 검증**: WSL docker build → container codex 0.130.0 + login OK + 실 codex exec ping→pong + input_tokens 24k→13k (~10k 절감). 21 신규 테스트 케이스. decisions.md §14 + 사용자 결정 8 + 자체 결정 8 |
| Phase 3 — A9 electron-client / A10 admin-console | ⬜ |
| Phase 4 — A11 test-ci / A12 demo-seed | ⬜ |
| 시연 리허설 + 발표 자료 | ⬜ |

## 빠른 진입

| 무엇을 보고 싶은가 | 어디로 |
|---|---|
| 프로젝트 한 페이지 요약 | 본 문서 |
| 모든 결정의 단일 진실 공급원 | [`docs/decisions.md`](docs/decisions.md) |
| 미해결 결정 (P0/P1/P2) | [`docs/decision-backlog.md`](docs/decision-backlog.md) |
| 핵심 알고리즘 (관심 모델) | [`docs/algorithms/cso-topic-traversal.md`](docs/algorithms/cso-topic-traversal.md) |
| 시스템 아키텍처 | [`docs/sdd/architecture.md`](docs/sdd/architecture.md) |
| DB 스키마 | [`docs/data/schema.md`](docs/data/schema.md) |
| 와이어프레임 (Mermaid) | [`docs/ux/wireframes.md`](docs/ux/wireframes.md) |
| 원본 SRS | [`SKKU_InSight_SRS.md`](SKKU_InSight_SRS.md) 또는 [`docs/srs/`](docs/srs/) (분할본) |
| 코드 작성 에이전트 운영 | [`AGENTS.md`](AGENTS.md) |

## 시연 시나리오 (1차 목표)

1. **신규 가입** → 동의 → CSO 12 클러스터 중 3개 선택 → Cold-start 대시보드 10개
2. **추천 카드 클릭·저장·숨김** → 관리자 콘솔에서 베이지안 사후·trace path 변화 관찰
3. **다음 active day 시뮬레이션** → 새 emerging 리프 생성 + active 승격
4. **관리자 콘솔에서 수집 실패 재실행** → 성공
5. **동의 철회** → 추천 중단 + 재동의/계정삭제 분기

## 빠른 시연 (A2 + A3 머지 후 가능)

```bash
# 1. .env 준비
cp .env.example .env
# JWT_SECRET (64+ random), POSTGRES_PASSWORD, ADMIN_BOOTSTRAP_PASSWORD 채우기
# placeholder 값은 lifespan validator 가 자동 차단 (decision-backlog C-22)

# 2. 깨끗한 부트
docker compose down -v
docker compose up -d postgres redis

# 3. DB 마이그레이션 (A2 + A3)
make migrate          # alembic upgrade head — A2 8 테이블 + A3 3 테이블 (cso_topic_parent · dynamic_leaf_topic · dynamic_leaf_topic_cso_topic)
make create-admin     # AdminUser 1행 (must_change_password=true)

# 4. CSO 3.4 임포트 + BroadInterest 12행 시드 (A3)
make import-cso       # ~14k 노드 + 12 cluster + 12 BroadInterest. 첫 호출 시 ~5분.
                      # 옵션: ARGS="--reset --refresh" 로 재구성

# 5. (A12 머지 후) 5+ 페르소나 + 14일 인터랙션 시드
make seed --full      # A12 산출

# 6. 모든 서비스 부트
docker compose up -d  # postgres + redis + api + worker + admin-console
                      # clickbait-detector 는 운영 결정 (CLICKBAIT_SERVICE_URL env 로 외부 호스팅)

# 7. (A9 머지 후) Electron 클라이언트
cd client && npm install && npm start
```

### A3 endpoint 검증 (CSO 임포트 후)

```bash
# 12 CSO 클러스터 (BroadInterest 시드 응답)
curl -s http://localhost:8000/topics/cso/clusters | jq '.clusters | length'   # 12
docker compose exec redis redis-cli GET 'cso:clusters:v1'                       # 24h Redis 캐시 JSON

# CSO 토픽 상세 + 다중 부모 (cso_topic_parent SOR)
ID=$(curl -s http://localhost:8000/topics/cso/clusters | jq -r '.clusters[0].cso_topic_id')
curl -s "http://localhost:8000/topics/cso/$ID/adjacent?hops=1" | jq '.topics | length'   # >0
curl -s "http://localhost:8000/topics/cso/$ID/descendants" | jq '.topics | length'       # 큰 수
```

검증 보조 (A2 완료 시점부터 사용 가능):

```bash
make test       # docker compose exec api pytest tests -v
make lint       # ruff + mypy --strict
make check-all  # 6 cross-check (api_docs / schema / env / error_codes / redis_keys / contracts)
```

기본 LLM provider 는 `mock` (deterministic fixture)이라 외부 API 키 없이 동작. 정식 API 시연 시 `LLM_PROVIDER=openai` + `OPENAI_API_KEY` 설정. multi-worker 시 `UVICORN_WORKERS=N` 환경변수 (Redis 분산 semaphore 가 전역 LLM 캡 보장).

## 기술 스택

| 레이어 | 선택 |
|---|---|
| Windows 클라이언트 | Electron 30+ + React 18 + TypeScript 5 + Vite |
| 관리자 콘솔 | Next.js 14 (App Router) |
| 백엔드 | FastAPI + Pydantic v2 + SQLAlchemy 2.x async |
| DB | PostgreSQL 16 + pgvector(미사용) + Redis 7 |
| 작업 큐 | RQ (Redis 기반) |
| 인증 | JWT (HS256, Access 15m + Refresh Redis 14d) + bcrypt(12) |
| LLM 어댑터 | Mock(default) / OpenAI / Anthropic / OpenRouter / CodexOAuth |
| 토픽 그래프 | NetworkX (in-memory CSO graph cache) |
| 외부 데이터 | **(v13 라운드)** LLM tool-use web search 단일 경로 — OpenAI Responses API `web_search` 도구. Source 테이블 = sentinel 1행 `llm_search` + publisher 정보 Document.raw JSONB |
| CI | GitHub Actions (lint + type + contracts cross-check + codegen diff) |

## 문서 구조 (54+ 파일)

```
docs/
├── decisions.md                     # ★ 결정 매트릭스 SOR
├── decision-backlog.md              # ★ P0/P1/P2 백로그
├── srs/         (10)                # IEEE 830 SRS 분할본
├── sdd/         (9)                 # 아키텍처·통신 규약·동시성·계약
├── api/         (8)                 # FastAPI 엔드포인트 명세
├── algorithms/  (7)                 # 베이지안·trace·라이프사이클·추천·...
├── data/        (5)                 # 스키마·ERD·시드
├── ops/         (5)                 # 배포·환경변수·CI·운영
├── security/    (5)                 # 인증·토큰·STRIDE·...
└── ux/          (4)                 # 와이어프레임·UI 상태·i18n·클라이언트 동작
```

## 라이선스 / 출처

- 본 프로젝트는 **성균관대 소프트웨어공학개론 조별과제 산출물**
- CSO (Computer Science Ontology) 데이터 © KMI Open University, CC BY 4.0
- 모든 외부 소스(arXiv·빅테크 블로그 RSS·네이버뉴스)는 각 사이트 이용 정책 준수 (메타데이터·요약·링크 중심 저장, 원문 무단 복제 금지 — NFR-25)
- DoRA 파인튜닝된 `A.X-4.0-Light` 낚시성 탐지 모듈은 본인 보유분으로 통합 예정

---

**다음 액션**: A9 electron-client 본문 구현 ([`prompts/08-A9-electron-client.md`](prompts/08-A9-electron-client.md)) — UI-01~05 + Electron `safeStorage` (OS 키체인) + 한국어 i18n + `client/src/generated/api.ts` codegen 결과 import (raw fetch 금지). A8 완료로 4 endpoint (`/recommendations/dashboard{,refresh}`, `/documents/{id}{,/summary}`) 안정 — A9 가 직접 호출 + 카드 클릭 시 `POST /events {event_type=click}` 가 trace 자동 생성 (A8 hook). A8 R3 실 GPT-5.5 시연 검증 통과 — 백엔드 데모 흐름 end-to-end 동작 확인됨. 자세히는 [`prompts/README.md`](prompts/README.md) 진행 트래커.
