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
| 낚시성 탐지 | **사용자 보유 DoRA 파인튜닝된 `A.x 4.0 light` 모듈을 통합**. 위치는 추후 사용자 공유 → `services/clickbait-detector` 컨테이너로 wrapping. **2차 문헌(테크 뉴스) 수집 단계 1차 정제에만 사용** | NFR-09, FR-30 직접 충족 |
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

## 5. 소스

| 카테고리 | 결정 | 한 줄 근거 |
|---|---|---|
| 학술 | **arXiv (cs.*) + OpenAlex + Semantic Scholar + DBLP** 4종 모두 | 학술 커버리지 극대화 |
| 빅테크 공식 채널 | **YAML registry(50–80개) + DB Source 테이블**. 사용자 관심 토픽과 교차해 동적 호출 | FR-24, EV-03 |
| 테크 뉴스 | **네이버뉴스 IT/과학 (BeautifulSoup 크롤링) + TechCrunch / The Verge / Wired + MIT Technology Review / IEEE Spectrum** | 한·영 병행 |
| 네이버 종속성 | **DB에서 토픽이 부모, 토픽 삭제 시 cascade 삭제** | 정책 명시 |

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
| 1 | A4 collection | 소스 어댑터, CollectionJob |
| 1 | A5 clickbait | DoRA 모듈 wrap |
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
| **M1** | arXiv 1일치 수집·낚시성 필터·관심도 업데이트 end-to-end |
| **M2** | 시드 페르소나로 대시보드 10개 (Cold-start + 점진 개선) |
| **M3** | Electron 6화면 + 관리자 웹 동작 |
| **M4** | AT-01~15 체크리스트 통과, 데모 스크립트 |
