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
