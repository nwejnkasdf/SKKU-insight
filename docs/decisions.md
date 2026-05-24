# 결정 매트릭스 (Single Source of Truth)

본 문서는 SKKU InSight 8라운드 결정 회의 결과를 압축한 단일 진실 공급원이다. 모든 후속 에이전트(A2~A12)가 코드를 짤 때 가장 먼저 봐야 할 결정 표이며, 본 문서와 SRS가 충돌하면 본 문서가 우선한다 (단, SRS의 FR/NFR/AT 식별자와 표 자체는 보존).

원본은 `/Users/hyojung/.claude/plans/iridescent-swimming-stardust.md` (v1.0, 2026-05).

## 1. 산출물·운영 전략

| 항목 | 결정 | 한 줄 근거 |
|---|---|---|
| 산출물 형태 | 풀스택 동작 데모 (시연 가능 수준) | 발표·시연이 평가의 핵심 |
| 코드 작성 주체 | 솔로 총괄 + 에이전트 분담. **Claude·Codex 양쪽에서 동일 프롬프트가 동작하도록 모델 의존 표현 회피** | 도구 종속을 최소화하기 위함 |
| 가용 시간 | 미정/유연 → 단계적 마일스톤(M0~M4)으로 관리 | 가용 시간 변동 흡수 |
| 한·영 정책 | UI 한국어 / 콘텐츠 한·영 병행. 요약·추천 이유는 한국어 | 1차 사용자가 한국어 화자 |

## 2. 기술 스택

| 레이어 | 결정 | 한 줄 근거 |
|---|---|---|
| Windows 클라이언트 | **Electron + React + TypeScript** | EV-01의 Mac 확장 시 코드 공유 가능 |
| 관리자 웹 콘솔 | **Next.js** (FastAPI 백엔드 공유) | UI-06이 별도 웹임을 만족 |
| 백엔드 | **FastAPI (Python)** + Pydantic + SQLAlchemy 2.x | LLM/스크래핑 라이브러리 풍부, OpenAPI 자동 생성 |
| DB | **PostgreSQL 16** (관계형 + JSONB) + **Redis 7** (세션·rate limit·작업 큐) | JSONB로 임베딩/JSON 메타 흡수 |
| 마이그레이션 | Alembic | SQLAlchemy 2.x 표준 |
| 작업 큐/스케줄러 | RQ 또는 APScheduler (Redis 기반) | 별도 인프라 없이 Redis만으로 운영 |
| 호스팅·실행 | **로컬 Docker Compose** 한 세트 (개발·데모 모두) | 발표용 단일 머신 부트 |
| 인증 | **JWT Access(15분) + Refresh(Redis 14일)** + **bcrypt(cost=12)** | NFR-15~17 충족, OWASP 권장 |
| Electron 토큰 보관 | **Electron `safeStorage` API** (OS 키체인) | 데스크톱 앱 표준 |
| Rate Limiting | **slowapi + Redis** — 로그인 5/분/IP, 가입 3/시간/IP, API 60/분/사용자 | NFR-22 보안 요구 충족 |
| 관리자 부트스트랩 | **환경변수 + CLI** (`make create-admin`) | 시드 자동화·재현성 |
| 테스트·CI | **pytest + vitest + GitHub Actions** 최소 CI | M4 자동화 검증 |
| **동시성 가정** | **10-20명 동시 사용자** 1차 운영 가정. single-flight + user-level mutex + atomic SQL + LLM semaphore + batch flush + consent cache + jitter 가드 ([`sdd/concurrency.md`](sdd/concurrency.md)) | NFR-12 + 정합성 보장 |
| DB 연결 풀 | api `PG_API_POOL_MAX=30`, worker `PG_WORKER_POOL_MAX=10` 분리 | worker가 api 요청 굶기지 않도록 |
| LLM 동시 호출 | 전역 `LLM_MAX_CONCURRENT=8`, 사용자당 `LLM_MAX_CONCURRENT_PER_USER=2` | 외부 API rate limit 보호 + 사용자 burst 방어 |

## 3. AI/LLM

| 항목 | 결정 | 한 줄 근거 |
|---|---|---|
| 낚시성 탐지 | **사용자 보유 DoRA 파인튜닝된 `A.X-4.0-Light` 모듈을 통합**. 모듈 위치 = `clickbait_module/`, 서빙 엔진 = **vLLM**(DoRA를 base에 사전 머지 후 일반 base로 로드 + continuous batching). 호스팅·transport는 운영 결정으로 backend는 `CLICKBAIT_SERVICE_URL` env로만 호출. **2차 문헌(테크 뉴스) 수집 단계 1차 정제에만 사용** | NFR-09, FR-30 직접 충족 |
| 그 외 LLM 작업 | `LLMProvider` 추상으로 **모델 슬롯**만 고정. 슬롯 `high` = 동적 리프 생성·병합, `medium` = 섹션형 요약·추천 이유. 모델은 슬롯 모두 `gpt-5.5` 단일. 슬롯 구분은 **`reasoning_effort`** 로 — `high` slot → `reasoning_effort=high`, `medium` slot → `medium`. `xhigh` 미사용 (latency + ChatGPT 5h 세션 한도). 코드 부트 default 는 `MockProvider` (CI 안전), `.env.example` 권고 default 는 `codex_oauth` (시연 narrative — 사용자 본인 ChatGPT 구독 활용) | 모델 의존 표현 회피, 재현성 + 시연 비용 절감 |
| LLM 어댑터 | `LLMProvider` 추상 + 5 구현체: **`MockProvider`** (CI fixture, Settings default), **`OpenAIAPIProvider`** (정식 API + Responses web_search), **`CodexOAuthProvider`** (`codex exec --json` subprocess wrap, 2026-05-18 본문, **시연 default — `.env.example` 권고**), `AnthropicAPIProvider`·`OpenRouterProvider` (stub). 환경변수 `LLM_PROVIDER` 로 토글. CodexOAuth 는 OpenAI 가 외부 도구 사용 **공식 허용** path ([openclaw docs/concepts/oauth] + [developers.openai.com/codex/cli/features]). 시연 부트 전 `make codex-login` 1회 → `~/.codex/auth.json` refresh_token 30일 유효 | 신뢰성 + 사용자 ChatGPT 구독 활용 + endpoint 회전 흡수 (codex CLI 가 처리) |
| 임베딩 | **미사용**. 토픽 유사도는 CSO 그래프 거리, 중복 제거는 URL/DOI + 제목 정규화 + Levenshtein | 인프라 단순화 |

## 4. 토픽·알고리즘

| 항목 | 결정 | 한 줄 근거 |
|---|---|---|
| 온보딩 카테고리 | **CSO 12 클러스터** 그대로 노출 (AI / Systems / Hardware / Theory / SE / Networks / IS·DB / IR / Security / HCI / Graphics·Multimedia / Computational Science) | FR-08, FR-13, BroadInterest 매핑 단순화 |
| CSO 임포트 | **PostgreSQL 영구 저장 + 서비스 시작 시 NetworkX 메모리 캐시 로드** | NFR-12 (p95 3초) 만족 |
| **사용자 관심 모델** | **CSO 그래프 위 traversal trace를 관심 상태 객체로 사용**. 단일 노드가 아닌 path 자체가 추론 단위. `UserCSOTraversal` 명시 entity. 행동이 root이고 명시 선택은 14 active day 한정 prior boost. 자세히는 [`algorithms/cso-topic-traversal.md`](algorithms/cso-topic-traversal.md) | Open Issue 5 해결 (인터뷰 신규 식별) |
| **카테고리 ↔ 슬롯** | **current/adjacent/proactive ↔ core/adjacent/discovery 1:1**. current = active trace path 끝 + 산하 leaf, adjacent = path 끝의 1-hop 그래프 이웃, proactive = path 외 영역 트렌드 + emerging 후보 | 일관·디버그 용이 |
| **시간 단위** | **모든 N일 임계는 active day 기준** (사용자 인터랙션 1+건 있는 날의 단조증가 카운터). trace + leaf 라이프사이클 + 베이지안 감쇠 통일 | 잠수 기간 대비 자연 reactivation |
| 관심 점수 모델 | **베이지안 (Beta-Bernoulli 우선)**. 단/장기를 다른 감쇠율의 두 관측창으로 명시 분리. **반감기 7/60 active days**. 구체 파라미터는 `interest_params.toml` 노출. **Trace 활성 path 위 조상 노드로 1-hop 0.5 감쇠 propagation** | Open Issue 1·2 해결 |
| Trace operation | **룰 기반(extend/retract/split/archive)**. LLM 호출은 retract/split 시 leaf 재배치에만 한정. 비용 cap 사용자당 일 ≤ 2 LLM 호출 | 비용·정확도 균형 |
| 리프 토픽 라이프사이클 | **D 하이브리드 우선** (신규 식별·병합만 LLM, 승격·강등은 룰). `LifecycleEvaluator` 인터페이스로 추상화하여 **B 배치 평가**도 갈아끼울 수 있게 유지. **신규 emerging은 active trace path 산하에서만 분기** | Open Issue 3 해결 |
| emerging 식별 주기 | **매 일일 수집 직후** (사용자별 LLM 1회/일) | 비용·신선도 균형 |
| 병합 평가 주기 | **주 1회** (사용자별 LLM 1회/주) | merged 빈도 |
| 이벤트 가중치 | `event_weights.toml` 구성 파일 (초기값: 클릭+1 / 체류≥2m+2 / 저장+5 / 숨김−3 / 관심없음−5) | 튜닝 가능 |
| 임계값 | `topic_lifecycle.toml` 구성 파일. **active day 기준 (7/2/21/7/30/90)**. 자세한 표는 `algorithms/leaf-topic-lifecycle.md` 및 `cso-topic-traversal.md` | 튜닝 가능 |
| 추천 슬롯 | core 5 / adjacent 3 / discovery 2 (SRS), fallback 룰 SRS FR-42·43 그대로. **core 5개 중 1개는 emerging leaf 우선** (C-4 해소). 신뢰도 임계 `recommendation.toml` | FR-37~43 |
| Cold-start | **LLM이 온보딩 입력(선택 CSO + 가입 메타)을 보고 첫 10개 추천 직접 생성**. 사용자가 첫 카드 클릭 시점에 그 cso_topic이 root인 trace 1건 생성 | UC-01 매끄러움 |

## 5. 소스 (v13 라운드 pivot 반영, 2026-05-11)

| 카테고리 | 결정 | 한 줄 근거 |
|---|---|---|
| **수집 모델** | **LLM tool-use (web search) topic-driven pull**. 사용자 trace 의 active leaf 를 LLM 에 입력 → LLM 이 web 검색 도구로 자료 fetch → Document 저장 | 프로젝트 원안 ("에이전트 기반 추천 시스템 하네스" + "ChatGPT 같은 검색 활용 + 토픽이 먼저고 문서가 나중") 회복 |
| **Source 테이블** | **sentinel 1행 `llm_search` 통일**. publisher 정보 (arxiv.org, openai.com 등) 는 `Document.raw` JSONB | 어댑터 폐기 후 Source 의미 축소. schema migration 최소 |
| **소스 어댑터 6종** | **폐기**. `app/source_adapters/` 디렉토리 생성 안 함. arXiv/OpenAlex/Semantic Scholar/DBLP/RSS/네이버BS4 어댑터 모두 미구현 | 본 모델에서 불필요. 향후 supplement 필요 시 별도 결정 |
| **NFR-25 정합 (외부 원문 무단 복제 금지)** | LLM 검색 prompt 에 "abstract 를 본인 말로 1~2문장 요약" instruction → `Document.summary` (schema.md ORM 컬럼명) = LLM self-summary | 추가 LLM 호출 없이 정합 |
| **클릭베이트 필터** | **default 비활성**. 사용자가 News 소스 명시 활성화 시 post-LLM filter 로만 동작 | LLM 이 1차 필터링하므로 기본 불필요. clickbait_module 코드 자체는 보존 |
| ~~빅테크 공식 채널 YAML registry~~ | ~~(폐기)~~ | v13 라운드 pivot |
| ~~네이버뉴스 IT/과학 BS4~~ | ~~(폐기)~~ | v13 라운드 pivot |
| ~~네이버 종속성 cascade~~ | ~~(폐기)~~ | v13 라운드 pivot |

## 6. 데이터·운영

| 항목 | 결정 | 한 줄 근거 |
|---|---|---|
| 시드 데이터 | **5+명 테스트 계정 자동 생성 스크립트**. 학생·연구자·교수 페르소나 + 14일치 인터랙션 로그 + 권한 분리 테스트(AT-13)용 일반 vs 관리자 계정 | M2~M4 시연 |
| 문서 구조 | **`docs/` 계층 테마별 분할** | 에이전트 컨텍스트 최소화 |
| 다이어그램 | **Mermaid 단일 소스**. 기존 SRS의 `assets/figure_*.png` / `assets/wire_*.png`는 PNG가 본 저장소에 동봉되지 않았으므로 동등 Mermaid로 대체. SRS 분할 파일의 PNG 링크는 IEEE 830 원형 보존 목적만으로 유지하되 상단에 안내 박스 (`docs/srs/*` 참조). | 재현성·플랫폼 독립 |

## 7. SRS Open Issue 해결 매핑

| Open Issue | 본 결정에서의 해결 |
|---|---|
| 1. 구체적인 관심 상태 점수 산식 | §4 베이지안 Beta-Bernoulli 모델 + `interest_params.toml`로 구체화 → `algorithms/interest-bayesian.md` |
| 2. 시간 감쇠 반감기와 이벤트별 가중치 | `event_weights.toml`(이벤트 가중치) + `interest_params.toml`(단/장기 감쇠율, **active day 기준**) 분리 |
| 3. 동적 리프 토픽 병합/폐기 세부 조건 | §4 D 하이브리드 + `topic_lifecycle.toml` 임계 표(**active day 기준**) → `algorithms/leaf-topic-lifecycle.md` |
| 4. Windows 데스크톱 앱 구현 프레임워크 | §2 Electron + React + TypeScript |
| 5. 사용자 × CSO 토픽 상태 머신·전이 룰 (인터뷰 신규 식별) | §4 traversal trace 모델 + active day 기준 → `algorithms/cso-topic-traversal.md` |

## 8. 에이전트 분할 (한눈에)

| Phase | 에이전트 | 산출 |
|---|---|---|
| 0 | A1 docs-bootstrap | 본 `docs/` 디렉토리 |
| 0 | A2 backend-foundation | FastAPI 부트, docker-compose, Alembic, 인증·동의·사용자, 보안 |
| 0 | A3 cso-topic | CSO 임포트, NetworkX 캐시, 그래프 탐색 API |
| 1 | A4 collection | **(v13 pivot)** LLM tool-use 검색 + Document/DocumentTopic/CollectionJob 영속 + dedup + jitter |
| 1 | A5 clickbait | **(v13 pivot)** DoRA 모듈 wrap. 1차 시연 default 비활성. 사용자 News 소스 활성화 시만 호출 |
| 1 | A6 interest-bayesian | 행동 로그 API, Beta-Bernoulli |
| 2 | A7 leaf-lifecycle | LifecycleEvaluator + LLM 프롬프트 |
| 2 | A8 recommendation | core/adjacent/discovery + fallback + Cold-start |
| 3 | A9 electron-client | UI-01~05 |
| 3 | A10 admin-console | UI-06 Next.js |
| 4 | A11 test-ci | pytest, vitest, GitHub Actions |
| 4 | A12 demo-seed | 페르소나 5+ + 14일 인터랙션 |

## 9. 마일스톤

| M | 완료 정의 |
|---|---|
| **M0** | docs/ 골격, FastAPI 부트, 인증 동작, CSO 임포트 |
| **M1** | **(v13 pivot)** LLM tool-use 검색 1일치 수집 + 관심도 업데이트 end-to-end |
| **M2** | 시드 페르소나로 대시보드 10개 (Cold-start + 점진 개선) |
| **M3** | Electron 6화면 + 관리자 웹 동작 |
| **M4** | AT-01~15 체크리스트 통과, 데모 스크립트 |

## 10. v13 라운드 — A4 Topic-driven Pivot (2026-05-11)

본 라운드는 A4 collection 본문 구현 직전 사용자 토의에서 합의된 **fundamental design pivot** 을 SOR 에 박는다. 이전 v1~v12 라운드의 학술 어댑터 중심 모델을 LLM tool-use 검색 중심 모델로 전환.

### 배경
프로젝트 원안은 "에이전트 기반 추천 시스템의 하네스 — ChatGPT 같은 검색 활용 — 토픽이 먼저고 문서가 나중". 그러나 IEEE 830 SRS 양식 작성 + A3 cso-topic engine 구현 과정에서 학술 IR 패턴(arxiv·openalex 어댑터 6종 push-from-sources)으로 표류. A4 본문 작성 직전 사용자가 인지 후 pivot 결정.

### Pivot 결정 매트릭스

| 영역 | v1~v12 (push-from-sources) | v13 (LLM tool-use pull) |
|---|---|---|
| 수집 모델 | 6 source 어댑터 cron pull | LLM 이 trace topic 받아 web 검색 도구 호출 |
| Source 어댑터 | arXiv / OpenAlex / Semantic Scholar / DBLP / RSS / 네이버BS4 (6종) | **폐기** |
| Source 테이블 | 소스별 row (50+) + SourcePolicy trust_level | sentinel 1행 `llm_search` + Document.raw publisher |
| Query 구성 | source 별 topic_keyword query | LLM 이 trace JSON 통째 받아 스스로 query 결정 (agent-driven) |
| LLM 호출 위치 | Document 별 cso_topic 매핑 (medium slot 1회/doc) | trace leaf 별 검색 + 매핑 통합 (medium slot 1회/leaf) |
| Clickbait 필터 | tech_news 모든 Document 강제 | 사용자가 News 소스 명시 활성화 시만 |
| NFR-25 정합 | metadata 만 저장 | LLM self-summary (prompt instruction) |
| 비용 모델 | API 호출 무료 + LLM 분류 medium slot | LLM 검색 호출 (검색 도구 사용량 + 토큰) |
| Mock fixture | 어댑터별 HTTP 응답 JSON | `prompt_hash → search_result JSON` (기존 MockProvider 패턴 확장) |
| 시연 발화 | "RSS 파서 6종이 모은 자료" | "GPT-5.5 가 web_search 도구로 검색해서 추천한다" (v13 round 2 갱신, Anthropic 미사용) |

### 사용자 결정 (4 batch AskUserQuestion)

| 결정 | 값 |
|---|---|
| LLM provider | Provider-agnostic toggle (LLM_PROVIDER env). MockProvider default + **OpenAI 정식 (GPT-5.5)**. Anthropic/OpenRouter/CodexOAuth 는 search_with_tools NotImplementedError — v13 round 2 (2026-05-16) lifespan 가드 (`_SUPPORTED_A4_PROVIDERS = {mock, openai}`) 가 boot 시 차단 |
| Query 구성 | LLM 이 user trace JSON 통째 받아 스스로 query 결정 (agent-driven) |
| Source 테이블 | sentinel 1행 `llm_search` + publisher Document.raw JSONB |
| Clickbait | 폐기 X. 1차 시연 default 비활성. 사용자가 News 소스 활성화 시 post-LLM filter |
| CollectionJob 단위 | (user × source). source 가 단일 sentinel 이라 실효 user 별 1건 |
| Trigger | daily cron + manual `POST /collection/jobs/me/run-now` (1/h). Onboarding/login 자동 trigger 미사용 |
| Jitter | deterministic hash(user_id, YYYYMMDD) % 300초 |
| Document.PK | UUID v4 + canonical_url partial unique (NOT NULL 일 때만) |
| Dedup 우선순위 | DOI → canonical_url → URL 정규화(utm_*/fbclid/gclid 제거 + lowercase host) → title 정규화 + Levenshtein ≥ 0.90 |
| 외부 실패 정책 | FAILED/SKIPPED 구분 + RQ retry 3회 exponential (60s/300s/900s) |
| /collection/jobs/me history | cursor pagination (default 20 / max 100) |
| /topics/{id}/documents | A4 가 같이 채움 (cross-cutting) |
| NFR-25 정합 | LLM 검색 prompt 에 "abstract 본인 말로 1~2문장 요약" instruction. 추가 호출 없음 |

### 폐기 또는 의미 변경 항목

- **§5 소스 매트릭스**: 학술 4종 / 빅테크 50-80개 / 테크 뉴스 NaverBS4 → **모두 폐기**
- **A4 산출**: 소스 어댑터 6종 + sources.yaml + RSS URL 검증 → **폐기**
- **decision-backlog P1-6** (네이버뉴스 야간 정리): pivot 으로 **무효** (NaverBS4 미사용)
- **decision-backlog P2-3** (RSS URL 검증): pivot 으로 **무효**
- **decision-backlog P2-4** (빅테크 50-80 확장): pivot 으로 **무효**
- **SRS FR-22~25** (소스 정의): 본 v13 라운드와 충돌. SRS 식별자는 보존하되 명세 내용을 v13 라운드 기준으로 해석 (헌법 §2 — SRS 식별자 보존, decisions.md 우선)
- **SRS NFR-25** (외부 원문 무단 복제 금지): self-summary 정책으로 정합. NFR 식별자 보존
- **UC-04** (Main Flow): 1번 항목 "학술 소스, 빅테크 공식 채널, 테크 뉴스에서 수집" → "LLM tool-use 로 사용자 trace 토픽 검색" 으로 의미 갱신

### 본 라운드가 만들거나 갱신하는 docs

| 파일 | 갱신 내용 |
|---|---|
| 본 파일 §5, §8, §10 | 위 표 |
| `decision-backlog.md` | P1-6 / P2-3 / P2-4 폐기 마킹, C-33 (pivot) 신규 |
| `srs/02-functional-requirements.md` | FR-22~25 명세 해석 박스 (식별자 보존) |
| `srs/03-nonfunctional-requirements.md` | NFR-25 self-summary 정합 박스 |
| `algorithms/cso-mapping.md` | "검색 query 자체가 topic" 으로 단순화 |
| `algorithms/clickbait-integration.md` | "사용자 명시 활성화 시만" 정책 명시 |
| `data/sources-registry.md` | sentinel 1행 + Document.raw publisher 로 재작성 |
| `data/schema.md` | Document.raw JSONB + Source sentinel 정합 |
| `sdd/architecture.md` | 다이어그램 갱신 (어댑터 6종 → LLM tool-use) |
| `api/collection.md` | 비즈니스 룰 갱신 |
| `prompts/03-A4-collection.md` | 재작성 (pivot 명세) |
| `AGENTS.md` / `README.md` / `docs/README.md` / `prompts/README.md` | A4 라인 동기화 |

## 11. A6 라운드 — Interest-Bayesian (2026-05-17)

본 라운드는 A6 interest-bayesian Phase 1 본문 구현과 Codex 2 라운드 감사 fix 를 SOR 에 박는다. [PR #18](https://github.com/nwejnkasdf/SKKU-insight/pull/18) (merge `a0a3fbf`) + [PR #19](https://github.com/nwejnkasdf/SKKU-insight/pull/19) docs drift fix (`a2930cf`).

### 결정 매트릭스 17건

| 영역 | 결정 | 한 줄 근거 |
|---|---|---|
| Decay schedule | **daily cron only (18 UTC = 03 KST)**. on-demand decay 미사용 | atomic UPSERT 부담 최소화. 시간 단위가 active day 라 일 단위 충분 |
| Dwell tick cap | **Redis Lua atomic INCR + EXPIRE** (`RedisKey.dwell_tick_count`). per-document day 단위 cap=4 | TTL 자연 만료. SQL UPSERT 보다 가볍고 race-free |
| 14-day boost 만료 | **daily cron 차감** + `boost_applied_at_active_day: Integer NULL` 컬럼. 만료 시 prior 원복 | onboarding prior boost 가 영구화되지 않도록 |
| Propagation 대상 | **cluster + 1-hop child (predecessor)**. trace 활성 path 위 조상 노드로 0.5 hop_decay | 1-hop 룰 일관. sibling 광역 오염 차단 |
| Propagation feature flag | **`INTEREST_PROPAGATION_ENABLED` env, default false**. A7 trace 활성화 후 true 로 토글 | A7 미완 단계에서 활성 path 정의 불가. 일단 self-only |
| Idempotency 정책 | **payload-hash + client_request_id**. match 시 200 + 기존 row, mismatch 시 409 `EVENT_DUPLICATE` | client retry safe. payload tamper 방어 |
| Not-interested 정렬 | **하이브리드 2 source** — (a) Bayesian P1-4 분배 (`UserInterestState`) + (b) `NotInterestedTopic` 최고 confidence 1건 INSERT | 명시 + 암묵 신호 동시 활용 |
| Cache invalidate | **`recommendation:{user_id}` 단일 키**. 토픽별 fanout 미사용 | A8 미완 단계. 단순 |
| Active_day 갱신 | **5 endpoint** (POST `/events`, POST `/events/batch`, POST `/feedback/save`, POST `/feedback/hide`, POST `/feedback/not-interested`). GET 은 무 영향 | NFR-12 latency + active day 의미 보존 |
| system_config 소유 | **A6 read-only loader + A10 admin UI**. A6 는 lifespan 부팅 시 1회 로드, 갱신 책임은 A10 | 운영 변경 흐름과 코드 모듈 책임 분리 |
| Batch 응답 | **207 Multi-Status** + `{items: [{event_id, accepted, error_code}], total_accepted}`. 부분 성공 허용 | client 가 entry 단위 실패 가능 (consent gate, idempotency mismatch 등) |
| Max events | **`/events/batch` max=50 entries per request**. 초과 시 422 | 단일 트랜잭션 안전. batch flush 5초 buffer 와 균형 |
| `/interest/state` | **max 50 leaf + bucket-sorted**. NFR-04 마스킹 (`score_tail=null`) | client display 양 + privacy |
| Atomic UPSERT | **단일 SQL `INSERT ... ON CONFLICT (cols) WHERE pred DO UPDATE`** (round 1 C-01 fix). UPDATE→INSERT 2-step lost update race 제거 | 12 partial UNIQUE 별 명시 |
| UserEvent ON CONFLICT | **`pg_insert(UserEvent).on_conflict_do_nothing(...).returning(event_id)`** + caller None-check (round 2 C-03 fix). batch race 시 앞선 entry 보존 | 전체 트랜잭션 rollback 회피 |
| Decay alpha floor | **GREATEST(:alpha_prior, computed)** (round 1 S-03 fix). child row +0.5 boost row 음수 차단 | 베이지안 prior 무효화 차단 |
| EventBuffer stop | **lock 안 `_stopped` 검사 + True 시 즉시 callback fallback** (round 1 S-04 fix) | lifespan shutdown race 차단 |

### 사용자 결정 (직접 합의)

| 결정 | 값 |
|---|---|
| Decay 빈도 | daily cron only (on-demand 거부) |
| Dwell cap 위치 | Redis Lua atomic (SQL UPSERT 거부) |
| 14-day boost 처리 | daily 차감 + `boost_applied_at_active_day` 컬럼 |
| Propagation 활성 | A7 도래 전까지 env false |
| Idempotency match 응답 | 200 + 기존 row (409 미사용) |
| Idempotency mismatch 응답 | 409 (`EVENT_DUPLICATE`) |
| Not-interested 추가 저장 | `NotInterestedTopic` 1 row 별도 (UserInterestState 분배만으론 부족) |
| Batch 최대 | 50 events |
| Batch HTTP 상태 | 207 (부분 성공 허용, 200 거부) |
| system_config 갱신 권한 | A10 admin UI 전담 (A6 는 read-only) |

### 폐기 또는 의미 변경 항목

- (없음 — v13 라운드 pivot 이후 A6 는 호환 확장만 수행)

### 본 라운드가 만들거나 갱신하는 docs

| 파일 | 갱신 내용 |
|---|---|
| 본 파일 §11 | 본 절 |
| `decision-backlog.md` | C-37 (round 1 8 fix) + C-38 (round 2 4 fix) 신규 + 카운트 36→38 + "다음 진입 모듈 A7" |
| `sdd/contracts.md` | `JobType.INTEREST_DECAY` + `ErrorCode` 2 + `RedisKey` 5 + Pydantic A6 schema 추가 |
| `sdd/architecture.md` | `### events (A6)` 섹션 + 외부 인터페이스 표에서 6 source 어댑터 행 제거 |
| `sdd/module-boundaries.md` | `app/events/` 행 + `app/interest/` 9 파일 분담 갱신 |
| `sdd/concurrency.md` §10 | `dwell_tick` Lua + `interest_decay_lock` + `system_config_cache` + `event_duplicate_cache` + 14-day boost daily 차감 5 행 추가 |
| `sdd/deployment.md` | 5 서비스 표 정렬 (clickbait-detector 표 외 별도 절) |
| `sdd/tech-stack.md` | `passlib` / `python-Levenshtein` / `beautifulsoup4` / `lxml` / `feedparser` / `tenacity` / `vcrpy` 6 행 제거 또는 v13 폐기 표기 |
| `sdd/api-conventions.md` | HTTP 상태 코드 표에 `207 Multi-Status` 행 추가 |
| `sdd/agent-orchestration.md` | Phase 표 A1~A6 ✅ 마킹 + 의존 그래프 status |
| `api/interest.md` | 9 endpoint base path 통일 + `EventResponse.error_code` 필드 + `BatchResponse` 스키마 |
| `data/schema.md` | `UserEvent.payload_hash` + `UserInterestState.boost_applied_at_active_day` 컬럼 명시 |
| `data/erd.mmd` | `SYSTEM_CONFIG` 엔티티 + `USER_EVENT` / `USER_INTEREST_STATE` attribute block 보강 |
| `data/cso-import.md` | R3-C03 `SEEDS` dict 5 라벨 + csv-quoted N-Triples parser 반영 |
| `algorithms/interest-bayesian.md` | `dwell_tick` Lua + propagation feature flag default false + NULLIF/alpha floor 룰 |
| `algorithms/cso-mapping.md` | 5 cluster seed 라벨 교체 (Operating Systems / Automata Theory / Interactive Computer Graphics / Multimedia Systems / Scientific Computing) |
| `ops/docker-compose.md` | 5432→5433 호스트 매핑 |
| `ops/runbooks.md` | `make seed` (A12 미완) 표시 |
| `security/threat-model.md` | 5432→5433 호스트 매핑 |
| `prompts/03-A4-collection.md` | LLM provider lifespan 가드 `{mock, openai}` 명시 + Anthropic `NotImplementedError` |
| `scripts/check_contracts.py` + `check_redis_keys.py` | A6 신규 `JobType` / Redis prefix 5 종 추가 |
| `AGENTS.md` / `decision-backlog.md` | C-급 카운트 32→38 + A6 fix 12 |

## 12. A7 라운드 — Leaf Lifecycle + Traversal (2026-05-17)

본 라운드는 A7 leaf-lifecycle + traversal Phase 2 본문 구현과 Codex R1 audit fix 를 SOR 에 박는다. PR-1 ~ PR-5 (6-commit PR-stack). trace operation 4 → 5 확장 (merge 신규 도입). docs/decision-backlog.md C-39 신규.

### 결정 매트릭스 23건

| # | 결정 영역 | 값 |
|---|---|---|
| 1 | PR 분할 방식 | A6형 6-commit PR-stack |
| 2 | Codex audit 라운드 수 | 3 라운드 (R1 본문 + R2 재감사 + R3 통합 시뮬레이션) |
| 3 | 통합 시연 LLM | 실 OpenAI GPT-5.5 호출 (수동 fixture, ~$0.5~1) |
| 4 | self-review | 미포함 — Codex audit 만 |
| 5 | INTEREST_PROPAGATION_ENABLED 토글 | 본문 PR-3 와 함께 default true |
| 6 | trace 생성 hook | A6 ingest_event_atomic hook (A8 진입 시 완성, plan TBD) |
| 7 | trace 3단계 강등 평가 | 하이브리드 — 1단계 ingest 즉시 / 2-3단계 daily 18 UTC cron |
| 8 | LLM 일 cap 정책 | Cap 폐지 (시연 단계, 운영 P2 backlog) |
| 9 | alembic 0005 | 필요 — UserCSOTraversal.merged_into_trace_id 컬럼 + ck_collection_job_type 갱신 |
| 10 | 실 OpenAI 시연 | 수동 fixture 시나리오 (3 LLM 실 호출) |
| 11 | ErrorCode 정의 위치 | contracts.py ErrorCode enum |
| 12 | A8 의존 TraversalEngine 설계 | 단일 protocol (write + read 통합, A8 재확인) |
| 13 | leaf 라이프사이클 전이 | 하이브리드 — 활성 신호 즉시 / 강등 daily cron |
| 14 | emerging 식별 trigger | collection daily cron 직후 hook (LEAF_LIFECYCLE_CRON="30 3 * * *") |
| 15 | trace_anchor 위반 처리 | 자동 거부 + 즉시 재호출 (retry cap=1, 빈 응답 fallback) |
| 16 | merged leaf 추천 노출 | 모든 추천 후보에서 제외 |
| 17 | trace merge operation | 신규 도입 (4 → 5 operation) |
| 18 | emerging input 범위 | 옵션 D — A4 collection union UserEvent click/save (24h) |
| 19 | emerging 검증 룰 | Strict — confidence ≥0.6 + supporting ≥3 + anchor + label dedup 0.75 |
| 20 | trace split path | T 단축 + T'=분기점+B |
| 21 | trace merge trigger | 룰 + LLM 결합 (path overlap ≥3 또는 proper subset) |
| 22 | trace merge winner | max(last_activity_active_day), tie 시 trace_id 작은 쪽 |
| 23 | trace merge LLM 호출 | Daily 18 UTC cron (A6 decay 와 동시각, user-mutex 분리) |

### 본 라운드가 만들거나 갱신하는 docs

| 파일 | 갱신 내용 |
|---|---|
| 본 파일 §12 | 본 절 (결정 매트릭스 23건) |
| `decision-backlog.md` | C-39 (A7 본문 + R1 fix 11건) 신규 + 카운트 38→39 + 다음 진입 = A8 |
| `sdd/contracts.md` | JobType.TRACE_MERGE + ErrorCode 5 + RedisKey 3 추가 |
| `ops/env-vars.md` | A7 Settings 33+ 항목 표 신규 (Leaf 식별/전이/병합 + Trace operation 5종 + propagation) |
| `api/topics.md` | A7 신규 ErrorCode 5건 오류 표 추가 (LLM parse 재사용 명시) |
| `algorithms/cso-topic-traversal.md §3` | trace operation 4 → 5 (+ merge) 표 갱신 |
| `algorithms/cso-topic-traversal.md §3.3` | split path 처리 — T 단축 + T'=분기점+B 로 갱신 |
| `algorithms/leaf-topic-lifecycle.md` | LLM 5 프롬프트 (identify_emerging / evaluate_merges / retract_reposition / split_dispatch / trace_merge_verify) |
| `data/schema.md UserCSOTraversal` | merged_into_trace_id 컬럼 명시 |
| `data/erd.mmd` | UserCSOTraversal self-FK 시각화 |
| `sdd/module-boundaries.md` | TraversalEngine 5 read API + LifecycleEvaluator + merge 메서드 |
| `prompts/06-A7-leaf-traversal.md` | A7 구현 prompt (계획 시점) |
| `AGENTS.md` / `README.md` | A7 완료 표기 + 마지막 갱신 일자 갱신 |

### 폐기 또는 의미 변경 항목

- §4 Trace operation 정의: extend/retract/split/archive 4 → **extend/retract/split/archive/merge 5** (룰 기반, merge 만 룰+LLM)
- §11 propagation feature flag: "default false (A7 도래 후 true)" → "default true (A7 PR-3 머지)"

## 13. A8 라운드 — Recommendation Engine (2026-05-17)

본 라운드는 A8 recommendation engine Phase 2 후반 본문 구현 + R1 self-review fix 를 SOR 에 박는다. decision-backlog C-40 신규.

### 결정 매트릭스 7건 (사용자 결정 4 + 자체 결정 3)

| # | 결정 영역 | 값 |
|---|---|---|
| 1 | PR-stack 패턴 | A7형 7-commit + 3 라운드 (PR-1 alembic+ORM / PR-2 본문 / PR-3 tests / R1 fix / R2 재감사 / docs drift / R3 통합 시연) |
| 2 | Document 섹션형 LLM 요약 캐시 위치 | **신규 테이블 `DocumentSummaryCache`** (alembic 0006). document_id PK + sections JSONB + reason_short + generator CHECK ('llm' | 'source_abstract'). DB 가 1차 SOR — Redis 보조 캐시 없음 |
| 3 | Cold-start 후 첫 trace 생성 hook | **A8 cold_start orchestrator + click hook 협업** (A7 결정 #6 plan TBD 완성). cold_start orchestrator 가 pseudo Document INSERT 시 mark. 첫 click 이벤트가 들어와 `app/interest/service.py:ingest_event_atomic` 의 traversal_lock 보유 구간 안 `mark_stale_if_idle` hook 옆에서 `DefaultTraversalEngine.ingest_event()` 위임 — 매칭 trace 있으면 last_activity 갱신, 없으면 새 trace |
| 4 | PUT /onboarding/interests (FR-55) | **A8 범위 외 — stub 유지** (현재 cluster 검증 + 202). 데모 시나리오 §1·§2 에 없고 settings 화면 (UI-05) 은 A8-v2 (electron-client) 범위. prior boost 갱신은 A6 bootstrap 협업, stale 마킹은 A7 evaluate_retract |
| 5 | sentinel `cold_start_pseudo` 활성화 | A2 alembic 0001 시드 행 (`name="cold_start_pseudo"`, `enabled=false`, `trust_level="low"`) 을 A8 가 본격 사용. cold_start orchestrator 가 pseudo Document INSERT 시 본 source_id FK 사용. `content_type='pseudo_cold_start'` enum (contracts.ContentType) 도 활성화 — candidates SQL AntiJoin 6번째 (일반 추천 경로 제외) |
| 6 | emerging quota 정책 | core 5 중 **1개는 emerging leaf 우선** (recommendation.toml `core_slot_quota.emerging_leaf_quota_in_core = 1`). emerging 후보 부재 시 active leaf 로 자동 회수 (recommendation-ranking.md §1.3). emerging vs active 구분은 candidates SQL 단일 호출로 `leaf.status` 컬럼 함께 fetch — race 차단 (§11.#3 사전 방어) |
| 7 | NFR-04 score 마스킹 정책 | Recommendation 테이블 `score` 컬럼 nullable Float **영속** (admin 노출용). 일반 사용자 응답 schema `RecommendationCard` 에는 score field **부재**. 응답 변환 (`engine._filled_slots_to_cards`, `engine._materialize_cards`) 시 명시 field 매핑만 (no `**row`) — `**ORM_row` 패턴이 우연한 leak 위험 (§11.#4 사전 방어) |

### §11 anti-pattern 5건 사전 방어 (R1 fix 최소화 핵심)

A2/A4/A6/A7 Codex 감사 누적 lesson 에서 A8 재발 가능 anti-pattern 을 본문 작성 시점에 사전 차단:

1. **Cache-before-commit (#1, A4 C-02 / A6 C-02 lesson)** — `service.get_dashboard` 의 `_build_and_cache` 가 `await db.commit()` → `await redis.setex(cache)` 순서. `cold_start_orchestrator.run_cold_start` 가 `session.commit()` 성공 후 `_set_status(completed)` + redis cache. DB commit 실패 시 cache/status 미적용.
2. **Recommendation daily UNIQUE race (#2, A6 C-03 lesson)** — `pg_insert(Recommendation).on_conflict_do_nothing().returning(recommendation_id)` + None-check + 동일 (user, doc, slot, today) lookup fallback. functional unique index `((created_at AT TIME ZONE 'UTC')::date)` 정합.
3. **emerging quota race (#3)** — candidates SQL 시점에 `leaf_status` 컬럼 함께 fetch (단일 SQL). emerging vs active 구분 in-memory partition. 별도 SQL 호출 X — A7 cron 의 status 전이 사이 race 차단.
4. **Score 컬럼 노출 (#4, NFR-04 위반)** — `RecommendationCard` schema 자체에 score field 부재 + `_materialize_cards` / `_filled_slots_to_cards` 가 명시 field 매핑만.
5. **Lock token race (#5, A7 R2-RG-3 lesson)** — `_RELEASE_LOCK_LUA` 상수 + uuid token + Lua atomic CAS DEL. cold_start orchestrator 의 onboarding_lock 명시 DEL 도 같은 패턴 권고 (TODO P2 — 현재 plain DEL, 자연 TTL 만료 의존).

### R1 self-review fix 1건 (commit 15883d1)

본문 commit 후 self-review 결과 §11 5건 사전 방어는 모두 적용 OK. 추가 점검에서 발견:

- **TopicChip dedup by (topic_id, type)** — `engine._fetch_topic_chips` 가 같은 doc 의 (cso, leaf) 매핑이 여러 confidence 행으로 존재 시 같은 chip 중복 추가 위험. `seen_per_doc: dict[UUID, set[tuple[UUID, str]]]` 추가 — chip append 전 (topic_id, type) tuple key 로 dedup.

### R2 Codex 외부 감사 결과 + fix (commit 099f837)

R2 Codex 독립 감사 — Critical 2 / Suggested 1 / Discussion 2 / Acknowledged 6.

**Critical #2 fix (engine.py)**: cold-start 완료 후 UTC 일자가 바뀌면 `_select_today_recommendations` 가 0 row → dashboard 빈 cards. 신규 `_select_latest_recommendations()` fallback — 가장 최근 생성일의 row 들 복원 (day_start~day_end 범위).

**Suggested #1 fix (cold_start.py)**: `_check_global_daily_cap` 의 INCR + EXPIRE 분리 → Lua atomic 단일 스크립트 (`_DAILY_CAP_INCR_LUA`). A6 dwell_tick Lua 패턴 동일. TTL 없는 영구 key 잔존 race 차단.

**P2 backlog 등재 (3건)**:
- **P2-22** cold_start_orchestrator concurrent race (Critical #1) — onboarding_lock TTL 만료 후 multi-worker race. UVICORN_WORKERS=1 1차 시연 영향 X. 운영 단계 inline Redis lock 또는 PG advisory_xact_lock.
- **P2-23** daily UNIQUE functional index volatility (Discussion #1) — **R3 시연 검증으로 해소** (PostgreSQL 16-alpine `((created_at AT TIME ZONE 'UTC')::date)` index 정상 통과).
- **P2-24** `_is_cold_start` 재진입 정책 (Discussion #2) — 14-day boost 만료 후 행동 신호 미발생 시 재진입 가능. cold-start.md §재활성화 정합 — 의도된 동작으로 결정. 운영 단계 marker 도입.

**⚪ Acknowledged 6건**: untargeted `on_conflict_do_nothing()` (A4 R3 lesson) / `_RELEASE_LOCK_LUA` CAS (A7 R2-RG-3 lesson) / `db.commit() → redis.setex()` 순서 (A4/A6 C-02 lesson) / RecommendationCard score 부재 (NFR-04) / candidates SQL `leaf_status` 단일 fetch (emerging quota race 방어) / interest hook traversal_lock + CAS (A7 R3 lesson) — 모두 의도된 사전 방어 패턴.

### R3 통합 시연 검증 + fix (commit ee627a2)

**환경**: docker compose (WSL2 docker, postgres:16-alpine + redis:7-alpine), `.env` LLM_PROVIDER=openai + OPENAI_API_KEY=실 키 + LLM_MODEL_HIGH=gpt-5.5.

**시연 흐름**: signup → login → consent (`{"consent_type":"personalization","agreed":true}`) → onboarding (AI/Systems/Theory 3 cluster) → polling `cold-start-status` → dashboard.

**시연 발견 결함 1건 fix** (`backend/app/llm_provider/openai.py`):

OpenAI 응답: `"Unsupported value: 'temperature' does not support 0.2 with this model. Only the default (1) value is supported."` GPT-5 series 가 chat/completions 에서 `temperature` parameter 미지원 (default 1.0 만 허용) + `max_tokens` → `max_completion_tokens` 변경.

fix: `model_name.lower().startswith("gpt-5")` 분기 — temperature payload omit (OpenAI default 1.0 자동) + max_tokens 키 변경. 다른 모델 (gpt-4o 등) 영향 없음. 사용자 결정 (재현성 미요구) 로 1.0 default 진행.

**검증 결과** (전 항목 통과):

| 항목 | 결과 |
|---|---|
| alembic 0001→0006 migrate | ✅ P2-23 functional index 통과 |
| 실 GPT-5.5 chat/completions 호출 | ✅ HTTP 200, 61s |
| 10 카드 응답 (실 논문/뉴스) | ✅ core 5 (Learning to Reason with LLMs / Transformers are SSMs / Llama 3.1 / NVIDIA Blackwell / IMO AI silver) / adjacent 3 (Model Context Protocol / Apple Intelligence / AI Scientist) / discovery 2 (AlphaFold 3 / Willow quantum chip) |
| 5/3/2 slot 분배 | ✅ target=actual, fallback_reason=null |
| Korean reason_short ≤80자 | ✅ 실측 23~31자 (prompt 60자 가이드 잘 따름) |
| NFR-04 score 마스킹 | ✅ 응답 'score' 키 0건 |
| 2회차 cache hit | ✅ cache=hit, cards=10 |
| cold_start=true 표시 | ✅ |
| sentinel `cold_start_pseudo` source 활용 | ✅ source_name="cold_start_pseudo" |
| R2 fix #2 UTC 경계 fallback 활성 | ✅ cold-start path 정상 로딩 |

### 본 라운드가 만들거나 갱신하는 docs

| 파일 | 갱신 내용 |
|---|---|
| 본 파일 §13 | 본 절 (결정 매트릭스 7건 + §11 사전 방어 + R1 fix) |
| `decision-backlog.md` | C-40 신규 + 카운트 39→40 + 다음 진입 = A8-v2 (또는 A8 R2/R3 별도) |
| `sdd/contracts.md` | 신규 enum/error code/Redis key 0 (모두 기존) |
| `sdd/agent-orchestration.md` | Phase 표 A8 ⬜ → ✅ + 다음 진입 A9 + Ownership 표 cold_start_job A8 ✅ |
| `data/schema.md` | Recommendation·RecommendationSlot "(A8 ⬜)" 마커 제거 + DocumentSummaryCache § 신규 추가 + daily UNIQUE 표현 정정 |
| `ops/env-vars.md` | A8 § 신규 (9 entry) + 표 행 + example block |
| `api/recommendation.md` | 본문 정합 (이미 SOR 보유 — 별도 변경 없음) |
| `algorithms/recommendation-ranking.md` | 본문 정합 (recommendation.toml 가 SOR) |
| `algorithms/cold-start.md` | 본문 정합 (orchestrator 흐름 + LLM prompt + validate) |
| `AGENTS.md` / `README.md` / `prompts/README.md` | A8 완료 ✅ 표기 + commit 4-stack 명시 |

### 폐기 또는 의미 변경 항목

- §4 추천 슬롯: "core 5/adjacent 3/discovery 2 (SRS), fallback 룰 SRS FR-42·43 그대로. core 5개 중 1개는 emerging leaf 우선" 그대로 — 본 라운드 코드 구현으로 SOR 활성화 (이전엔 docs 명세만).
- §4 Cold-start: "LLM 이 온보딩 입력(선택 CSO + 가입 메타)을 보고 첫 10개 추천 직접 생성. 사용자가 첫 카드 클릭 시점에 그 cso_topic 이 root 인 trace 1건 생성" → A7 결정 #6 plan TBD 본 라운드 완성 (interest.service.py:ingest_event_atomic 안 hook).

## 14. CodexOAuth 라운드 — Subprocess Wrap + reasoning_effort Fix (2026-05-18)

본 라운드는 `CodexOAuthProvider` 본문 작성 + 직전까지 dead intent 였던 `reasoning_effort` payload 처리 fix + `service_tier=fast` default 적용 을 SOR 에 박는다. 직접 fetch (openclaw docs, openai/codex repo) + 호스트 codex 0.130.0 실 호출 (JSONL event format / `-c service_tier=fast` accept) 로 검증 후 결정. decision-backlog C-41 신규.

### 사용자 결정 (직접 합의) 8건

| # | 결정 영역 | 값 | 근거 |
|---|---|---|---|
| 1 | CodexOAuth 방식 선택 | **`codex exec --json` subprocess wrap** (방식 B). 자체 PKCE OAuth (방식 C) X | openclaw 의 primary path 도 native codex CLI subprocess wrap. endpoint 회전 시 codex CLI 업데이트로 자동 흡수. 자체 PKCE 는 `auth.openai.com/oauth/*` flow 까지만 구현해도 실제 LLM call endpoint 가 docs 미공개 — 모방 risk |
| 2 | 합법성 평가 | **공식 허용 path 로 간주**. 시연 규모 (10-20 사용자) 에서 OpenAI 자동 차단 위험 무시 가능 | openclaw 공식 문서 인용: "OpenAI explicitly supports subscription OAuth usage in external tools and workflows like OpenClaw" |
| 3 | 모델 slot 매핑 | **모든 slot `gpt-5.5` 동일** + `model_reasoning_effort` 로 high/medium 구분 | 사용자 원래 의도 — 모델은 단일 (gpt-5.5), 깊이만 다르게 |
| 4 | reasoning_effort 값 | **high → "high", medium → "medium"**. `xhigh` 미사용 | xhigh 는 latency 길어지고 5시간 ChatGPT 세션 한도 초과 우려. 100$ 요금제도 부족할 수 있음 (사용자 본인 경험) |
| 5 | 시연 default | **`.env.example` 권고 = `codex_oauth`** (Settings 코드 default 는 `mock` 유지 — CI 안전) | 발표 narrative — "사용자 본인 ChatGPT 구독으로 동작". 비용 0. 단 CI / 신규 운영자 환경에서 부트 안 깨지도록 코드 default 는 mock |
| 6 | `web_search` mode default | **`cached`** (default), `live` 토글 옵션 | 시연 안정성 우선. `live` 는 실시간 web 크롤링이라 latency·비용 변동 큼. cached 가 부족한 신선도 보이면 시연 30분 전에 live 토글 |
| 7 | `service_tier` default | **`fast`** (default, 모든 codex_oauth 호출에 `-c service_tier=fast`) | 5시간 ChatGPT 세션 한도 안에서 단일 호출 latency 줄이는 게 시연 안정성에 직접 영향. fast = 우선순위 큐 + 빠른 응답. 가능 값 `fast/default/flex/scale/priority`. codex 가 모델/요금제 호환 자동 처리. 사용자 본인 PC `~/.codex/config.toml` 도 동일 설정 |
| 8 | `--ignore-user-config` + `--ignore-rules` 항상 적용 | **모든 codex 호출에 두 flag 박음** | backend prompt 가 SOR — NFR-04 마스킹 / 한국어 응답 / FR-44 reason 형식 등 자체 제어. 사용자 본인 `~/.codex/config.toml` 의 personality (`pragmatic` 등) 가 backend 응답 스타일에 잡스러운 영향 주는 것 차단. API 검증된 prompt 가 이미 codex 자체 prompt 없이도 충분 (호스트 실측: 호출 정상 응답). `-c key=value` override 와 충돌 없음 (override 우선순위 더 높음 — 직접 호출로 검증) |

### 결정 매트릭스 8건 (자체 결정)

| # | 결정 영역 | 값 | 근거 |
|---|---|---|---|
| 1 | `codex` sandbox 정책 | `read-only` | codex 가 backend 컨테이너 파일을 임의 mutation 못 하도록. workspace-write 는 시연 외 케이스 |
| 2 | `--cd` workdir | `/tmp/codex-runtime` | git repo 검출 회피 + 외부 파일 격리. /tmp 또는 dedicated tmpfs |
| 3 | docker volume mount | `${HOME}/.codex:/root/.codex` rw | refresh token 자동 갱신 위해 rw 필수. ro 면 codex 가 auth.json 갱신 실패 → 1시간 후 access 만료로 모든 호출 깨짐 |
| 4 | lifespan binary 검증 | `codex --version` strict (fail-fast) | `codex login status` 까지는 검증 X — refresh 만료가 자주 발생하므로 binary 존재까지만 startup 차단 |
| 5 | `--output-schema` 사용 | response_format=json 시 generic schema 임시 파일, search_with_tools 는 `_SEARCH_OUTPUT_SCHEMA` 강제 | structured output 보장 — Codex 가 free-form text 대신 JSON parse 가능 형태로 응답 |
| 6 | JSONL event parser | `item.completed` 의 `agent_message` text 만 final_text 로 합침. `web_search_call` 등 다른 item 은 skip | LLMResponse 인터페이스 호환 — caller 는 final text 만 필요 |
| 7 | usage 매핑 | `prompt_tokens = input_tokens`, `completion_tokens = output_tokens + reasoning_output_tokens` | reasoning_output_tokens 도 사용량 차감 — budget guard 정합 |
| 8 | LLM_PROVIDER 토글 구조 | **글로벌 단일** (slot 별 분기 X) | slot 별 분기는 fallback narrative 복잡. 운영 단계 backlog |

### reasoning_effort fix (사용자 원래 결정 코드 반영, 2026-05-18)

직전까지 `openai.py` 가 `reasoning_effort` 를 payload 에 안 박아서 high slot 도 medium slot 도 OpenAI default (보통 medium) 로 동작 중. 사용자 결정이 dead intent 였던 결함 fix.

| 항목 | 변경 |
|---|---|
| `Settings.LLM_REASONING_EFFORT_HIGH/MEDIUM` 신규 | 사용자 결정값 `high` / `medium` default. `.env` 토글로 xhigh 같은 다른 값 가능 (운영자 책임) |
| `openai.py:complete` | GPT-5 series 분기에 `payload["reasoning_effort"] = settings.LLM_REASONING_EFFORT_HIGH or _MEDIUM` 추가 (top-level key, Chat Completions API spec) |
| `openai.py:search_with_tools` | Responses API nested `payload["reasoning"] = {"effort": settings.LLM_REASONING_EFFORT_HIGH}` 추가 |
| `codex_oauth.py` 양 메서드 | `-c model_reasoning_effort=<value>` argv override |
| OpenAI spec 출처 | `openai-python/types/chat/completion_create_params.py` + `shared_params/reasoning.py` 직접 fetch. 가능 값 `none / minimal / low / medium / high / xhigh` |

### 본 라운드가 만들거나 갱신하는 파일

| 파일 | 변경 |
|---|---|
| 본 파일 §3 LLM 어댑터 + §14 | LLM 어댑터 행 갱신 + 본 라운드 §14 신규 |
| `decision-backlog.md` | C-41 (codex_oauth 본문 + reasoning_effort fix) 신규 + C-급 카운트 40→41 |
| `backend/app/llm_provider/codex_oauth.py` | stub → ~500줄 본문 |
| `backend/app/llm_provider/openai.py` | reasoning_effort payload 분기 (chat + responses) |
| `backend/app/llm_provider/__init__.py` | docstring 갱신 |
| `backend/app/config/__init__.py` | Settings 6 신규 (`LLM_REASONING_EFFORT_HIGH/MEDIUM` + `CODEX_CLI_PATH` + `CODEX_SANDBOX_MODE` + `CODEX_WORKDIR` + `CODEX_WEB_SEARCH_MODE`) |
| `backend/app/lifespan.py` | `_SUPPORTED_A4_PROVIDERS` 확장 + `_validate_codex_cli()` 신규 |
| `backend/Dockerfile` | Node.js 20 + `npm i -g @openai/codex` |
| `docker-compose.yml` | api + worker 둘 다 `~/.codex` volume mount |
| `Makefile` | `make codex-login` + `make codex-status` target |
| `backend/.env.example`, `.env.example` (루트) | 시연 default `LLM_PROVIDER=codex_oauth` 권고 + CODEX_* 4 env |
| `docs/ops/env-vars.md` | 표 5 행 갱신 + example block 갱신 |
| `backend/tests/llm_provider/test_codex_oauth.py` | 신규 14 케이스 |
| `backend/tests/llm_provider/test_openai_reasoning_effort.py` | 신규 6 케이스 |
| `backend/tests/llm_provider/test_lifespan_provider_guard.py` | codex_oauth 케이스 갱신 (allowed/binary-missing/exit-nonzero) |
| `backend/tests/llm_provider/test_openai_search.py` | reasoning 미전송 → reasoning 전송 가드로 갱신 |

### 폐기 또는 의미 변경 항목

- §3 LLM 어댑터 표의 "CodexOAuth (local experimental) — 로컬 실험·개인 토이 빌드에만 권장하고 배포·시연 환경의 기본값이 아니다" → **본 라운드로 정정** — subprocess wrap 본문 완성 + 시연 default 권고
- 직전 v13 round 2 (2026-05-16) "reasoning 파라미터 미전송 — OpenAI default 위임" → 본 라운드 reasoning_effort fix 로 폐기. 사용자 원래 결정 (high/medium 분리) 이 코드에 반영
- `CODEX_OAUTH_TOKEN` env → legacy 표시. codex CLI 가 `~/.codex/auth.json` 으로 자체 관리

## 15. A8-v2 라운드 — UserProfile + Discovery Fusion + Reincarnation Pivot (2026-05-19)

본 라운드는 discovery slot 2의 본질을 **사용자 흥미 *궤적의 교차점*에서 새 방향성을 발굴**로 pivot. 기존 구현 (`Source.trust_level='high' + cso_topic NOT IN trace_path + freshness DESC` 정렬) 이 SRS FR-41 "**잠재적으로 관심 있을 수 있는** 새 주제" 의 의도를 약하게 해석한 상태였음 — 개인화 신호가 0이고 사실상 "신뢰성 있는 최신 트렌드 노출". 본 라운드 후 discovery slot 1 = Fusion (archive × current cross-product) + slot 2 = Reincarnation (`score_tail >= 0.6` archived trace 부활). core 5 + adjacent 3 은 안정성 base 로 그대로 유지.

### 배경 — 학술 trend + 본 라운드 고유 angle

조사한 5종 paper (PersonaX ACL'25 / LettinGo KDD'25 / PURE / Guided Profile Generation NAACL'24 / Temporal Profiling) 가 모두 "행동 시퀀스 → offline LLM cron → 자유 텍스트 페르소나 캐시 + online 캐시 사용" 패턴으로 수렴 — 우리 daily cron + DB 영속 모델과 정확히 일치. Serendipity 인터뷰 연구 (RecSys-related '25, 17명 grounded theory) 의 3-dimension framework (Fortuitous + Refreshing + Enriching) 에서 "taste reincarnation" 이 reincarnation slot 의 직접 근거. **archive × current cross-product 융합** 은 paper 들이 못 본 본 라운드 고유 angle — 두 시점 표현을 cross-product 해서 새 영역을 추론.

### 시스템 정체성

> AI 가 매일 사용자의 archived trace 와 active trace 를 cross-product 해서, **두 영역이 만나는 새 학습 path** — 예: Graph Algorithms (과거) × Memory Management (현재) = **Memory-bounded Algorithms** — 를 discovery 카드로 제시한다. 학문이 가장 크게 도약하는 지점 (ML+Systems=MLSys, HCI+AI=대화형 AI) 의 메커니즘을 개인 추천 차원에서 구현한다.

### 사용자 결정 매트릭스 (11건)

| # | 영역 | 결정 |
|---|---|---|
| 1 | Discovery slot 본질 | slot 1 = Fusion (archive × current cross-product), slot 2 = Reincarnation (`score_tail >= 0.6` archived trace 부활) |
| 2 | core / adjacent 변경 | 없음 (안정성 base 유지) |
| 3 | 비율 5:3:2 | SRS FR-38 그대로 유지 |
| 4 | UserProfile 노출 정책 | **ORM/schema 만**, endpoint·UI 없음 (향후 노출 결정 시 추가) |
| 5 | UserProfile schema | 구조화 6 필드 (3 텍스트 + 3 JSONB) + 메타 컬럼 |
| 6 | LLM input archive 범위 | `score_tail >= 0.6` archived trace 만 (강한 신호로 종료된 것만) |
| 7 | LLM cron 시각 | daily 19 UTC (A6/A7 18 UTC 와 분리) |
| 8 | LLM provider | 기존 `LLM_PROVIDER` env 재사용 (시연 default `codex_oauth`, CI `mock`) |
| 9 | Reasoning effort | high (추론 깊이 필요) |
| 10 | Cold-start (archive 0건) | 다중 active trace 시 cross-trace fusion 시도, 단일 trace 시 기존 trust=high trend fallback |
| 11 | LLM output | `--output-schema` (codex_oauth) / `response_format=json_schema` (openai) strict 강제 + CSO 노드 ID 매핑 강제 |

### 자체 결정 (구현 세부, 10건)

| # | 영역 | 결정 |
|---|---|---|
| 1 | Lock 키 | `RedisKey.user_profile_generation_lock(user_id)` = `lock:user_profile_gen:{user_id}`, TTL 180s |
| 2 | Cache 키 | `RedisKey.user_profile_cache(user_id)` = `user_profile:{user_id}`, TTL 1h (SETEX, daily cron 후 DEL) |
| 3 | Profile upsert | 단일 `pg_insert(UserProfile).on_conflict_do_update(index_elements=["user_id"])` — PK 만이라 partial unique 불필요 |
| 4 | Reincarnation gap | `USER_PROFILE_REINCARNATION_GAP_DAYS_MIN=7` — 너무 최근 archive 제외 (자연 망각 시간 부재) |
| 5 | Input archive cap | `USER_PROFILE_INPUT_ARCHIVE_MAX=8` — token 폭주 가드, score_tail DESC 정렬 상위 N |
| 6 | Pydantic strict | `extra="forbid"` + `additionalProperties=False` 강제 (codex `--output-schema` 호환) |
| 7 | Lua atomic release | uuid4 token CAS DEL — A7 R2-RG-3 패턴 답습 |
| 8 | Per-user try/except | 사용자별 commit (batch 통째 rollback 회피 — A6 C-03 lesson) |
| 9 | cache-before-commit 회피 | `db.commit() → redis.delete(recommendation_cache)` 순서 |
| 10 | Bridge CSO 매핑 가드 | LLM 응답의 `bridge_cso_topic_id` ∈ `cso_graph` 검증, 위반 candidate 제거 + 전체 매핑 실패 시 `None` 반환 |

### Discovery slot 본문 fallback chain (engine.build_dashboard)

```
profile = await get_user_profile(...)
pool = []

# slot 1 (Fusion)
if profile.fusion_candidates 의 valid bridge ∈ cso_graph:
    pool += query_discovery_fusion(bridge_cso)
elif profile.broadening_seeds[0]:
    pool += query_discovery_fusion(seed_cso)

# slot 2 (Reincarnation)
archived_trace = get_top_archived_trace(score_tail_min=0.6, gap_days_min=7)
if archived_trace:
    pool += query_discovery_reincarnation(tail_cso, archived_leaves)
elif profile.deepening_seeds[0]:
    pool += query_discovery_fusion(seed_cso)

# 모든 경로 빈 list 시
if not pool:
    pool = query_discovery_trend(list(trace_path_csos))   # 기존 rule
```

### SRS 정합 (3 박스)

- **FR-41 "잠재적으로 관심 있을 수 있는"** — 원문 의도 회복. 본 라운드 변경이 FR-41 정합 강화 (해석 박스: [`srs/02-functional-requirements.md`](srs/02-functional-requirements.md)).
- **04-data-model.md Table 7** — UserProfile 부재 정합 박스. SRS 표는 원형 보존하되 본 라운드 신규 entity 가 [`data/schema.md`](data/schema.md) 에서 SOR (헌법 §3 SRS 식별자 보존 + decisions 우선).
- **NFR-04** — discovery 카드 `reason_short` 한 줄 노출 룰 정합 박스. UserProfile 자체 비노출 (admin / 일반 사용자 UI 모두), 시간/강도 추상화 표현만 카드 옆 표시.

### Anti-pattern 회피 (A6/A7/A8 lesson 누적, 9건)

| # | Anti-pattern | 회피 |
|---|---|---|
| 1 | Cache-before-commit (A4 C-02, A6 C-02, A8 §11 #1) | `db.commit() → redis.delete(recommendation_cache)` 순서 강제 |
| 2 | Read-then-write race (A6 C-01) | UserProfile upsert = `pg_insert.on_conflict_do_update` 단일 SQL |
| 3 | Batch IntegrityError rollback (A6 C-03) | per-user try/except + 사용자별 commit |
| 4 | Lock release race (A7 R2-RG-3) | Lua atomic `GET+DEL` CAS (uuid4 token), `_RELEASE_LOCK_LUA` 상수 |
| 5 | Daily UNIQUE race (A8 §11 #2) | UserProfile PK=user_id 만이라 N/A |
| 6 | NFR-04 score leakage (A8 §11 #4) | discovery 카드 명시 필드 매핑 (no `**row`), reason_short 거부 키워드 강화 |
| 7 | LLM provider 분기 lifespan 가드 (C-41) | 기존 `_SUPPORTED_A4_PROVIDERS` 재사용 (codex_oauth 포함) |
| 8 | LLM hallucination — CSO 그래프 부재 ID | `bridge_cso_topic_id ∈ cso_graph` 매핑 가드, 위반 candidate 제거 + 전체 실패 시 None |
| 9 | 토큰 폭주 (활성 사용자 archive 누적) | `USER_PROFILE_INPUT_ARCHIVE_MAX=8` cap + `score_tail DESC` 정렬 |

### 본 라운드가 만들거나 갱신하는 파일

| 파일 | 변경 |
|---|---|
| `backend/app/db/models/user_profile.py` | 신규 (~70줄 ORM) |
| `backend/alembic/versions/0007_a9_user_profile.py` | 신규 (~130줄 DDL + ck_collection_job_type 7-value 갱신) |
| `backend/app/db/models/__init__.py` | UserProfile export 추가 |
| `backend/app/contracts.py` | JobType.DAILY_USER_PROFILE_GENERATION + RedisKey.user_profile_generation_lock + RedisKey.user_profile_cache + ErrorCode 2종 |
| `backend/app/config/__init__.py` | Settings 7 신규 env (USER_PROFILE_*) |
| `backend/app/profile/__init__.py` + `schemas.py` + `config_loader.py` + `prompt_builder.py` + `service.py` | 신규 5 파일 (~900줄) |
| `backend/app/worker/jobs/user_profile.py` | 신규 (~150줄 daily cron) |
| `backend/app/scheduler.py` | JOB_REGISTRATIONS A8-v2 entry 추가 (6 → 7 cron) |
| `backend/app/recommendation/candidates.py` | `query_discovery_fusion` / `query_discovery_reincarnation` / `query_discovery_trend` 신규, 기존 `query_discovery` deprecated alias |
| `backend/app/recommendation/engine.py` | `_build_discovery_pool_raw` helper + build_dashboard 의 discovery 분기 본문 교체 |
| `backend/app/recommendation/reasons.py` | NFR-04 거부 키워드 강화 (버킷 / score_tail / 신뢰도 추가) |
| `backend/app/traversal/queries.py` | `get_archived_traces_with_score` / `get_top_archived_trace` / `get_descendant_archived_leaves` 신규 |
| `.env.example` + `backend/.env.example` | USER_PROFILE_* 7 env 추가 |
| `docs/decisions.md §15` | 본 절 신규 |
| `docs/decision-backlog.md C-42` | A8-v2 라운드 entry 신규 |
| `docs/sdd/contracts.md` | JobType / RedisKey / ErrorCode 표 갱신 |
| `docs/data/schema.md` UserProfile § | 신규 |
| `docs/algorithms/recommendation-ranking.md §Discovery` | 본문 pivot (fusion / reincarnation / fallback chain 룰) |
| `docs/srs/02-functional-requirements.md` | FR-41 정합 박스 |
| `docs/srs/04-data-model.md` | UserProfile 부재 정합 박스 |
| `docs/srs/03-nonfunctional-requirements.md` | NFR-04 정합 박스 |
| `docs/ops/env-vars.md` | A9 § + 7 env 행 + 골격 갱신 |
| `docs/api/recommendation.md` | A8-v2 cron 내부 ErrorCode 표 추가 (endpoint 부재 명시) |

### 폐기 또는 의미 변경 항목

- §4 추천 슬롯 "discovery 2" 의미 — 기존 "trust=high trend" → "Fusion 1 (archive × current cross-product) + Reincarnation 1 (score_tail >= 0.6 archive)". core 5 + adjacent 3 의미는 그대로.
- §13 A8 라운드 결정 #7 (NFR-04 score 마스킹 정책) — 본 라운드 reason_short 거부 키워드 강화로 확장 적용 (UserProfile context).

### Codex R1 audit + R1 fix 7건 + P2 backlog 4건 (2026-05-19, [PR #25](https://github.com/nwejnkasdf/SKKU-insight/pull/25) merge commit `63f2cdde`)

본문 commit 직후 `codex:rescue` (GPT-5.5) 외부 audit 수행. **Critical 2 + Suggested 7 + Nit 2 = 11 issue** 식별. R1 fix 7건 즉시 적용, 큰 변경 4건 (LLMProvider protocol 시그니처 / archived_at 별도 컬럼 / candidate_pool 매핑 / cache key version) 은 P2 backlog (P2-26~29) 로 등재.

#### R1 fix 7건

| # | Audit 등급 | 위치 | Fix |
|---|---|---|---|
| 1 | **Critical** | `app/config/__init__.py` + `worker/jobs/user_profile.py` | `USER_PROFILE_LOCK_TTL_SECONDS` 180→360s — 2x LLM timeout 마진. 직전 180s == LLM_REQUEST_TIMEOUT_SECONDS 라 LLM 호출 도중 lock 만료 race 위험 |
| 2 | **Critical** | `app/recommendation/engine.py` | fusion + reincarnation pool 통합 → `_build_discovery_pools` 가 tuple 반환 + `_build_fusion_subslot` + `_build_reincarnation_subslot` 별도 + build_dashboard 가 각 [:1] concat → slot 별 1개씩 강제. `_resolve_seed_id` helper 가 active path 제외 + cso_graph 멤버십 검증 |
| 3 | Suggested | `app/worker/jobs/user_profile.py` | cache invalidate 를 finally 안 별도 try/except 로 분리 + `committed` flag — redis 실패가 committed DB write 를 rollback 처리하지 않음 |
| 4 | Suggested | `app/recommendation/engine.py:_resolve_seed_id` | Fusion bridge_id 가 `trace_path_csos` (active path 노드) 안이면 거부 — core 슬롯 후보 중복 차단 |
| 5 | Suggested | `app/recommendation/engine.py:_build_fusion_subslot` + `_build_reincarnation_subslot` | fallback 이 candidate 존재 (`fusion_used=True` 직접 마킹) 가 아니라 SQL rows 결과 기반 (`if rows: return rows`) — 빈 결과 시 다음 fallback 진행 |
| 6 | Suggested | `alembic/versions/0007_a9_user_profile.py:downgrade` | `raise NotImplementedError` — downgrade 시 `daily_user_profile_generation` row CHECK violation 차단. 운영 rollback 은 별도 SOP |
| 7 | Nit | `app/profile/prompt_builder.py` | system prompt 안 raw `score_tail >= 0.6` 문구 → "강한 흥미로 종료된 보관 궤적 (충분히 큰 흥미 신호만 사전 필터)" 자연어 |

#### P2 backlog 4건 (decision-backlog P2-26 ~ P2-29)

| ID | 영역 | 상태 |
|---|---|---|
| P2-26 | `LLMProvider.complete` 시그니처에 `output_schema` 인자 추가 (codex `--output-schema` / openai `response_format=json_schema` strict 모드 API 수준 연결) | **활성** — protocol 변경이 다른 provider 전체 영향 (단 Anthropic 미사용 결정 2026-05-19, codex_oauth/openai/openrouter/mock 만 본격 구현). 후속 세션. |
| P2-27 | `UserCSOTraversal.archived_at_active_day` 별도 컬럼 추가 (A7 execute_archive 가 user.active_day_counter 저장) | **✅ 해소 (C-44, 2026-05-19, [PR #28](https://github.com/nwejnkasdf/SKKU-insight/pull/28))** — alembic 0008 + ORM + operations.execute_archive(active_day_counter) + execute_merge loser archive + queries COALESCE fallback. |
| P2-28 | Fusion bridge_cso 가 `cso_candidate_pool` 멤버십 강제 (graph 전체 외) | **✅ 해소 (C-44, 2026-05-19, 옵션 A2 카테고리별, [PR #28](https://github.com/nwejnkasdf/SKKU-insight/pull/28))** — alembic 0008 user_profile.candidate_pool_ids JSONB + CSOTopicCandidatePool schema (fusion/deepening/broadening) + validation 강화. |
| P2-29 | `RedisKey.recommendation_cache` key 에 `UserProfile.generated_at` 버전 토큰 포함 + read 시점 stale 거부 | **활성** — multi-worker race window. single-worker 시연 환경 실효 무해, 운영 단계 적용. 후속 세션. |

#### 검증 결과 (R1 fix 후)

- `ruff check backend/app` (151 files): All checks passed
- `mypy --strict backend/app` (151 files): no issues
- 6 cross-check scripts: 모두 통과 (`check_contracts` / `check_env` 131=131 / `check_schema` 25 tables / `check_error_codes` 45=45 / `check_redis_keys` raw f-string 0 / `check_api_docs` 55=55)
- `pytest tests/profile/`: **57 passed** (52 base + R1 audit_regressions 5 추가)
- WSL docker compose 통합 시연: alembic 0007 통과 + lifespan 부트 (`cso_graph nodes=14707 edges=44131 clusters=12 / provider=codex_oauth / system_config_loaded=true`) + scheduler `JOB_REGISTRATIONS count=7` (A8-v2 신규 `user_profile_generation_job` 포함) + 모든 신규 모듈 surface import 검증 통과

#### 후속 (별도 세션)

- 5 persona × 실 GPT-5.5 fusion 카드 데모 (broad_interest ID 매핑 + 행동 데이터 SQL seed 필요)
- Codex R2 재감사 (R1 fix 회귀 검증) + P2-26~29 본격 fix
- A9 electron-client (UI-01~05 + safeStorage + 한국어 i18n + codegen api.ts)

## 16. C-53 라운드 — Fusion bridge BFS + Reincarnation softmax + Weekly promotion (2026-05-24)

### 배경

A8-v2 (C-42, §15) 가 discovery slot 을 "Fusion 1 + Reincarnation 1" 으로 pivot. 다만 두 sub-slot 결정 알고리즘이:
- **Fusion bridge**: LLM 1회로 결정 (candidate_pool 안에서 bridge_cso 선택) — LLM hallucination 위험 + LCA root 가까운 bridge 가능
- **Reincarnation**: `get_top_archived_trace` deterministic top-1 — 매일 같은 archived trace 반복

사용자 의도 (디자인 논의 — 본 PR 직전):
1. "trace↔trace meet in the middle" 으로 bridge 결정 (그래프 알고리즘, LLM 의존 0)
2. "반감기 없애고 매일매일 갱신" (discovery freshness decay 자체 제거)
3. "reincarnation 다양성 = sigmoid + T" (softmax sampling)
4. "강한 신호 (save) → core 부활" (discovery/adjacent → core promotion)

### 결정

**Fusion bridge_cso 결정** ([`backend/app/traversal/fusion_bridge.py`](../backend/app/traversal/fusion_bridge.py)):
- trace↔trace meet in the middle BFS
- 두 path 의 `user_interest_state.long_score` DESC top_k (default 5) 출발점
- path 전체 visited 마킹 + 공유 노드 제외 (Fusion = path 밖 새 교차)
- 외향 BFS — superTopicOf + relatedEquivalent edge 양방향 활용
- 첫 만남 노드 = bridge. tie 시 두 path 거리 sum 최소
- max_hops=3 안 만나지 않으면 None → query_discovery_trend fallback
- LCA root 문제 자연 회피 — path 위 노드 visited 마킹으로 root 도 frontier 제외

**Reincarnation softmax sampling** ([`backend/app/profile/sampling.py`](../backend/app/profile/sampling.py)):
- `P(trace_i) = exp(score_tail_i / T) / Σ exp(score_tail_j / T)`
- **T=0.3 default** — score 0.6~1.0 분포 기준 top 70~80% weight, 다양성 충분
- 수치 안정 — max 정규화 (overflow 방지) + T clip (0.05 minimum, 극단값 회피)
- `_build_reincarnation_subslot` 가 `get_top_archived_trace` → `softmax_sample_archived_trace` 교체

**Weekly promotion** ([`backend/app/worker/jobs/weekly_promotion.py`](../backend/app/worker/jobs/weekly_promotion.py)):
- 주 1회 cron (WEEKLY_PROMOTION_CRON="0 18 * * 0" 일요일 18 UTC = 월요일 03 KST)
- 직전 7-day UserEvent.save → Recommendation.origin_type/origin_ref JOIN
- **Reincarnation save** (origin_type='reincarnation') → `trace.status: archived → active`, path 보존
- **Fusion save** (origin_type='fusion') → 새 active trace INSERT (path=[bridge_cso])
- dedup ((origin_type, origin_ref) 같으면 1번만) + idempotent (이미 active 또는 같은 path 있으면 skip)
- active cap 무제한 (사용자 결정)

**Recommendation origin metadata** ([alembic 0010](../backend/alembic/versions/0010_c53_weekly_promotion.py)):
- `recommendation.origin_type` (varchar(40) NULL) + `recommendation.origin_ref` (uuid NULL)
- `ix_recommendation_origin` partial index (origin_type IS NOT NULL)
- 'reincarnation' = archived trace_id / 'fusion' = bridge_cso_topic_id / NULL = core/adjacent/trend

### 사용자 결정 9건 (디자인 논의)

| # | 결정 | 출처 |
|---|---|---|
| 1 | bridge_cso = LLM 의존 X, 그래프 알고리즘 | 사용자 "trace↔trace 지향 탐색" |
| 2 | meet in the middle BFS | 사용자 "meet in the middle 로는 어렵나?" |
| 3 | path 전체 출발 (top_k=5 limit) | 사용자 "trace 길어질경우 감당 안되니까 top 5" |
| 4 | edge = superTopicOf + relatedEquivalent | 사용자 "이거 뭐여" 질문 + 둘 다 사용 결정 |
| 5 | Reincarnation softmax + T=0.3 | 사용자 "sigmoid 적용하고 T값 조절" |
| 6 | active cap 무제한 | 사용자 "active cap 무제한" |
| 7 | promotion 주 1회 | 사용자 "주 1회" |
| 8 | Reincarnation = trace 그대로 부활 | 사용자 "기존 archived trace 그대로 부활" |
| 9 | Fusion promotion = 새 active trace INSERT | 디자인 논의 결론 (path 위 grafting 부자연) |

### 자체 결정 5건

| # | 결정 | 근거 |
|---|---|---|
| 1 | sub-slot 별 freshness 차등 (C-51) | 사용자 의도 "최신성 추천 핵심" (discovery decay X = C-52) |
| 2 | LLM fusion document fetch 별개 PR (C-54) | 사용자 의도 4 ("두 trace 컨텍스트 LLM fetch") = LLM 추가 호출 + DocumentTopic 매핑 협업, scope 분리 |
| 3 | tie break = path 거리 sum 최소 → UUID lexicographic | 두 영역 균형 + deterministic |
| 4 | softmax 수치 안정 (max 정규화 + T clip 0.05) | overflow 방지 + 극단값 회피 |
| 5 | weekly_promotion_job LLM X | SQL UPDATE/INSERT 만 — 빠름, 실패 isolation 단순 |

### 본 PR 범위 외 (C-54 별개 PR — 사용자 의도 4 따라)

- LLM fusion document fetch — 두 trace + leaf 컨텍스트 → 신규 LLM 호출 → fusion 후보 document fetch + bridge_cso 매핑 DocumentTopic INSERT
- 의문 #2 (bridge_cso 의 DocumentTopic 매핑 부재 가능성) 해결
- 시연 narrative 강화 후 결정

### 빈틈 4건 (운영 단계 — 시연 후 평가)

1. BFS top_5 출발의 quality — `long_score` decay 후 narrow 분포 가능. 시연 결과 보고 조정 (tail-only / centroid / quartile sampling)
2. bridge_cso valid 해도 DocumentTopic 매핑 0 → fallback trend (자연, 디자인 결함 X)
3. softmax T 실 데이터 조정 (시연 sampling 분포 보고 0.1~0.5 사이 조정)
4. promotion 무제한 cap 운영 단계 가드 (월 max promote / 자동 archive) — 별개 PR

### 영구화

| 변경 | 위치 |
|---|---|
| `find_fusion_bridge` algorithm | `backend/app/traversal/fusion_bridge.py` (신규) |
| `softmax_sample_archived_trace` | `backend/app/profile/sampling.py` (신규) |
| `apply_fusion_bridge_override` | `backend/app/profile/service.py` |
| UserProfile job 통합 | `backend/app/worker/jobs/user_profile.py` |
| Recommendation origin metadata | `backend/app/db/models/recommendation.py` + `backend/app/recommendation/engine.py` |
| weekly_promotion_job | `backend/app/worker/jobs/weekly_promotion.py` (신규) + `backend/app/scheduler.py` (8 cron) |
| Settings 4 env + JobType + alembic | `backend/app/config/__init__.py` + `backend/app/contracts.py` + `backend/alembic/versions/0010_c53_weekly_promotion.py` (신규) |
| `.env.example` 4 env | `.env.example` + `backend/.env.example` |

PR #39 (5 commits) + PR #40 (alembic revision id long name fix) merge commit `c9bb667` / `10ebaa5`.

### C-53 followup — 직전 라운드 평가 fix 4건 (2026-05-24)

직전 PR #39/#40/#41 머지 직후 자체 평가에서 빈틈 5건 식별, 사용자가 4건 fix 결정 (1번 = 표준 패턴 / 2번 = active 도 softmax / 4번 = 동시성 보강 / 5번 = discovery sub-table 제거). 3번 (path≤k 분기에서 long_score 무시) 은 현재 구현 유지.

| # | fix | 위치 | 근거 |
|---|---|---|---|
| 1 | meet-in-the-middle BFS 표준 패턴 — 같은 depth 양방향 동시 확장 후 한 번에 meet 검사. 거리 비대칭 + next_a×next_b 동시 만남 모두 포착 | [`backend/app/traversal/fusion_bridge.py:147~`](../backend/app/traversal/fusion_bridge.py) | 직전 expand_a → check → expand_b → check 가 표준 bidirectional BFS 아님. tie_break (거리 sum 최소 + UUID lex) 그대로 유지 |
| 2 | active_trace 선택 = `max(score_tail)` → softmax sampling (T=`REINCARNATION_SAMPLING_TEMPERATURE`). archived 와 동일 기준 다양성 | [`backend/app/profile/service.py:apply_fusion_bridge_override`](../backend/app/profile/service.py) | 사용자 결정 "active_trace 도 같은 기준의 softmax". `softmax_sample_archived_trace` → `softmax_sample_trace` rename (archived/active 공용) |
| 3 | weekly_promotion 동시성 보강 — per-user `traversal_lock` (uuid4 token + NX SET + Lua atomic CAS release). daily_lifecycle / trace_merge / interest hook 와 같은 lock 키 공유 | [`backend/app/worker/jobs/weekly_promotion.py:_run`](../backend/app/worker/jobs/weekly_promotion.py) | 사용자 결정 "동시성 미흡하면 보강". 같은 사용자 trace mutation (status archived→active / 새 active INSERT) 이 다른 worker job 과 race 차단. asyncio.gather 병렬화는 lock contention 만 늘려 미적용 (10~20명 sequential 충분) |
| 4 | `[freshness.discovery]` sub-table 폐기 + 코드 상수 `_UNITY_FRESHNESS` 도입. `freshness_for_slot("discovery")` 분기로 항상 1.0 반환 | [`backend/app/recommendation/config_loader.py`](../backend/app/recommendation/config_loader.py) + [`backend/app/config/recommendation.toml`](../backend/app/config/recommendation.toml) | 사용자 결정 "제거". discovery freshness 무의미 (C-52 정정 결과) 인데 toml placeholder sub-table 잔존 = drift. core/adjacent sub-table 은 유지 (각 30d/0.3, 14d/0.2) |

### C-53 followup 빈틈 외 평가 보존 (3번 = 현재 유지)

`_select_top_k_path_nodes` 의 path 길이 ≤ top_k 분기는 path 순서 사용 (long_score 정렬 X). 사용자 결정 "지금 구현 그대로". 짧은 path 는 BFS 부담 적어 실질 영향 X.

PR [#42](https://github.com/nwejnkasdf/SKKU-insight/pull/42) merge commit `5ef0656`.

## 17. C-54 라운드 — Fusion bridge_cso 영역 fresh Document fetch (2026-05-24)

### 배경

C-53 의 §빈틈 #2 — "bridge_cso 가 valid 해도 DocumentTopic 매핑 0 → fallback trend (자연, 디자인 결함 X)" 를 LLM web_search 도구로 채우는 흐름. 사용자 의도 4 ("두 trace 컨텍스트 LLM fetch") 실현.

기존 흐름: BFS 결정한 bridge_cso 가 cso_graph 멤버라도 그 cso 와 매핑된 Document 가 0개면 `query_discovery_fusion` 빈 풀 → fallback trend. fusion narrative 가 카드로 안 나타남.

본 라운드: UserProfile cron 안에서 `apply_fusion_bridge_override` 가 BFS bridge 결정 직후 LLM web_search 도구로 bridge 영역 fresh 자료 1~5건 fetch + Document/DocumentTopic INSERT. 다음 dashboard 조회 시 fusion 카드가 자연 채워짐.

### 사용자 결정 6건

| # | 결정 | 근거 |
|---|---|---|
| A | **A1** — UserProfile cron 안 (apply_fusion_bridge_override 끝에) | profile 영속화와 cohesion + dashboard 다음날 조회 시 이미 준비 + C-40 cold_start orchestrator 패턴 답습 |
| B | **B2** — bridge_cso + 두 path 라벨 + 각 trace 최근 saved Document 제목 3개 | 사용자 신호 농도 + LLM 이 narrative 잡기 좋음 |
| C | **C1** — 기존 collection_job 의 LLM schema 재사용 (provider.search_with_tools, prompt 본문 미수정) | 코드 단순 + parser/INSERT 흐름 통합 |
| D | bridge 매핑은 CSO 노드 위에서만 시작, 이후 사용자 인터랙션 시 다른 trace 처럼 동적 leaf 생성 가능 | DocumentTopic INSERT 시점에는 bridge_cso 단일 매핑 (LeafTarget(parent=bridge, leaf=None)). weekly_promotion 으로 새 active trace `path=[bridge_cso]` 생성 후 사용자 인터랙션 시 leaf_lifecycle 자연 흡수 |
| E | **E1** — 매일 fresh fetch (조건 가드 X) | narrative "매일 새 발견" 정합 |
| F | **F1** — LLM 실패 시 fusion_candidates 보존 + Document INSERT 안 함 → dashboard 빈 풀 fallback trend | BFS 결정 살림 |

### P1 (사용자 결정) — prompt dedup hint

직전 30일 fusion 카드 의 Document.canonical_url + title list 를 prompt context `trace_json["seen_urls"]` / `["seen_titles"]` 에 박음. 기존 collection prompt §2 dedup hint ("입력의 seen_urls 또는 seen_titles 리스트와 겹치는 자료 회피") 가 자연 적용 — 매일 같은 자료 반복 차단 + LLM 자율적으로 다양성 확보.

### 자체 결정 6건

| # | 결정 | 근거 |
|---|---|---|
| 1 | provider 인터페이스 미확장 — `search_with_tools(trace_json, leaf_label, ...)` 그대로 재사용 | 5 provider 구현체 영향 0. leaf_label 자리에 bridge_label, trace_json 안에 fusion 맥락 |
| 2 | anti-pattern 답습 — `_insert_document_idempotent` (on_conflict_do_nothing) + `_upsert_document_topic` (greatest confidence) | A4 R2-C01/C02/S04 패턴 |
| 3 | fetch document max = 5 | collection_job cap 동일 |
| 4 | `FUSION_FETCH_ENABLED` default true + `FUSION_FETCH_MAX_DOCUMENTS=5` + `FUSION_FETCH_RECENT_URLS_WINDOW_DAYS=30` Settings env | 운영 시점 토글 가능 |
| 5 | `apply_fusion_bridge_override` 시그니처 — provider / enabled / 2 cap 인자 optional default off | 기존 caller 영향 0 (테스트 fixture 호출 등) |
| 6 | F1 실행 — `try/except Exception: logger.warning` + 정상 흐름 유지 | BFS 결정 살림, 다음날 cron 재시도 |

### 빈틈 — 시연 후 평가

1. **bridge_cso 가 같으면 같은 자료 반복** — P1 dedup hint (직전 30일 fetch URL 회피) 가 해소. soft instruction 이므로 LLM 이 무시할 수 있으나 실측 시 강도 조정 (window_days 늘리거나 hard filter 도입)
2. **LLM hallucination 으로 web 자료 0건** — 자연 빈 풀 fallback trend (F1 과 동일 처리)
3. **cross-leaf 매핑 충돌** — `_insert_document_idempotent` 의 canonical_url UNIQUE + `_upsert_document_topic` greatest confidence 가 자연 해소
4. **fetch 한 Document 가 사용자 dashboard 에 다음날 한 번만 노출** — Recommendation 의 (user, doc, slot, date) UNIQUE 가 자연 dedup. 같은 fusion 카드 가 다음날 사라지면 weekly_promotion 흐름과 정합

### 영구화

| 변경 | 위치 |
|---|---|
| `fetch_fusion_documents` + `fetch_trace_saved_titles` + helpers | `backend/app/profile/fusion_fetch.py` (신규) |
| `apply_fusion_bridge_override` 시그니처 확장 + 호출 추가 | `backend/app/profile/service.py` |
| `user_profile_generation_job` 호출 인자 확장 | `backend/app/worker/jobs/user_profile.py` |
| Settings 3 env | `backend/app/config/__init__.py` |
| `.env.example` 3 env | `.env.example` + `backend/.env.example` |

스키마 변경 0 (alembic 없음).

PR #(TBD) merge commit `(TBD)`.

