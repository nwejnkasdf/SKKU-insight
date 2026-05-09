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
4. 다음 날 일일 수집(arXiv·OpenAlex·Semantic Scholar·DBLP·빅테크 RSS·네이버뉴스) → 사용자별 trace에 맞춘 새 추천 10개
5. 시간이 지나면 **CSO 그래프 위 traversal trace**가 깊어지고 (예: AI → NLP → LLM), 그 끝에 사용자별 **dynamic leaf 토픽**이 분기 (예: "RAG 변형 기법", "Speculative Decoding")

## 핵심 디자인 포인트 (차별화)

- **Traversal trace = 관심 상태 객체**. 단일 노드가 아니라 사용자가 CSO 그래프 위를 흘러간 path 자체가 하나의 관심을 표현. 추천·요약·LLM 프롬프트 모두 trace 단위로 추론.
- **3 카테고리 ↔ 3 슬롯 1:1**. `current/adjacent/proactive` (모델) ↔ `core/adjacent/discovery` (추천) 가 자연스럽게 매핑.
- **Active day 회계**. 모든 시간 임계(라이프사이클·베이지안 감쇠)가 wallclock이 아니라 "사용자가 인터랙션한 날"의 단조증가 카운터 기반. 시험기간 잠수해도 trace가 깨지지 않음.
- **Beta-Bernoulli 베이지안 + 1-hop propagation**. atomic SQL UPSERT로 race condition 방어, leaf 활동이 부모 노드 점수로 propagate.
- **DoRA 파인튜닝 `A.x 4.0 light` 낚시성 모듈** + LLM은 **Mock provider default** + OpenAI/Anthropic/OpenRouter/CodexOAuth 토글.
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
| **코드 작성 (Phase 0a~4)** | 🟡 대기 — 사용자 명령 시 시작 |
| 시연 데이터 시드 + 발표 자료 | ⬜ |

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

## 빠른 시연 (코드 완성 후)

```bash
# 1. 깨끗한 부트
docker compose down -v
docker compose up -d postgres redis

# 2. DB·CSO·관리자 시드
make migrate          # alembic upgrade head
make import-cso       # CSO 14k 노드 임포트
make create-admin     # 관리자 계정 생성

# 3. 5+ 페르소나 + 14일 인터랙션 시드
make seed --full

# 4. 모든 서비스 부트
docker compose up -d  # api + worker + clickbait-detector + admin-console

# 5. Electron 클라이언트
cd client && npm install && npm start
```

기본 LLM provider는 `mock` (deterministic fixture)이라 외부 API 키 없이 동작. 정식 API 시연 시 `LLM_PROVIDER=openai` + `OPENAI_API_KEY` 설정.

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
| 외부 데이터 | arXiv API · OpenAlex · Semantic Scholar · DBLP · 빅테크 RSS 30+ · 네이버뉴스 BS4 |
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
- DoRA 파인튜닝된 `A.x 4.0 light` 낚시성 탐지 모듈은 본인 보유분으로 통합 예정

---

**다음 액션**: [`AGENTS.md`](AGENTS.md) §"에이전트 분할" 표의 Phase 0a (A2-stub)부터 시작 — `backend/app/contracts.py` + 모든 endpoint signature stub만 작성하는 단일 세션. 사용자 검수 + OpenAPI codegen 후 본격 Phase 0b 진입.
