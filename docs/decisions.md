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
| 그 외 LLM 작업 | `LLMProvider` 추상으로 **모델 슬롯**만 고정. 슬롯 `high` = 동적 리프 생성·병합, `medium` = 섹션형 요약·추천 이유. 1차는 누구나 클론 즉시 부트되도록 `MockProvider`(deterministic fixture)를 기본값으로 사용하고, 실제 LLM 호출이 필요한 기능은 `OpenAIAPIProvider` 등 정식 API로 토글 | 모델 의존 표현 회피, 재현성 |
| LLM 어댑터 | `LLMProvider` 추상 + 5 구현체: **`MockProvider` (default, CI/시연 fixture)**, `OpenAIAPIProvider`, `AnthropicAPIProvider`, `OpenRouterProvider`, **`CodexOAuthProvider` (local experimental)**. 환경변수 `LLM_PROVIDER`로 토글. CodexOAuth는 비공식 OAuth 세션을 사용하므로 로컬 실험·개인 토이 빌드에만 권장하고 배포·시연 환경의 기본값이 아니다 | 신뢰성 + 본인 빌드용 도피선 둘 다 확보 |
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
| 4 | PUT /onboarding/interests (FR-55) | **A8 범위 외 — stub 유지** (현재 cluster 검증 + 202). 데모 시나리오 §1·§2 에 없고 settings 화면 (UI-05) 은 A9 (electron-client) 범위. prior boost 갱신은 A6 bootstrap 협업, stale 마킹은 A7 evaluate_retract |
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

### R2/R3 후속 (별도 세션)

- **R2 Codex 재감사** — Codex CLI 또는 `/ultrareview` 로 본문 11 파일 + tests 9 파일 독립 감사. self-review 가 catch 못 한 결함 발견 가능.
- **R3 통합 시연** — 사용자 OPENAI_API_KEY 설정 후 `docker compose up -d` + signup → consent → onboarding 3 cluster → cold-start 8s SLA 폴링 → `GET /recommendations/dashboard` → 10 카드 + 5/3/2 slot + 한국어 reason ≤80자 + NFR-04 score 미노출 + cold_start=true → 2회 호출 cache hit → 첫 click 이벤트 → UserCSOTraversal trace 1 row 자동 생성 검증.

### 본 라운드가 만들거나 갱신하는 docs

| 파일 | 갱신 내용 |
|---|---|
| 본 파일 §13 | 본 절 (결정 매트릭스 7건 + §11 사전 방어 + R1 fix) |
| `decision-backlog.md` | C-40 신규 + 카운트 39→40 + 다음 진입 = A9 (또는 A8 R2/R3 별도) |
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

