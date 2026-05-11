# 시스템 아키텍처

본 파일은 SKKU InSight의 컴포넌트 분할과 책임을 명세한다. SRS §3.7의 그림과 본 결정 매트릭스 §2를 종합한다. 데이터 흐름은 [`data-flow.md`](data-flow.md), 모듈별 인터페이스 계약은 [`module-boundaries.md`](module-boundaries.md), 배포는 [`deployment.md`](deployment.md), 기술 스택 핀은 [`tech-stack.md`](tech-stack.md) 참고.

## 전체 구조 (ASCII)

```
┌──────────────────┐   HTTPS    ┌──────────────────────────────────┐
│  Electron App    │◀──────────▶│  FastAPI Backend                 │
│  (React+TS)      │   JWT      │  ┌────────────────────────────┐  │
│  safeStorage     │            │  │ auth / consent / user      │  │
└──────────────────┘            │  ├────────────────────────────┤  │
                                │  │ topic-engine (CSO graph)   │  │
┌──────────────────┐            │  ├────────────────────────────┤  │
│  Next.js Admin   │◀──────────▶│  │ collection-orchestrator    │  │
│  Console         │  JWT(adm)  │  ├────────────────────────────┤  │
└──────────────────┘            │  │ leaf-lifecycle (LLM)       │  │
                                │  ├────────────────────────────┤  │
                                │  │ interest-bayesian          │  │
                                │  ├────────────────────────────┤  │
                                │  │ recommendation-engine      │  │
                                │  └────────────┬───────────────┘  │
                                └────┬───────────┼──────────┬──────┘
                                     │           │          │
                          ┌──────────┴────┐  ┌───┴────┐  ┌──┴──────┐
                          │ Postgres 16   │  │ Redis  │  │ Workers │
                          │  + pg cron    │  │        │  │ (RQ)    │
                          │ + NetworkX 캐시│  │        │  │         │
                          └───────────────┘  └────────┘  └────┬────┘
                                                              │
            ┌─────────────────────────────────────────────────┴────────────┐
            │  Source Adapters    │  Clickbait DoRA   │  LLM Adapter         │
            │  arXiv / OpenAlex / │  (vLLM 기반,       │  Mock (default) /    │
            │  S2 / DBLP / RSS /  │   외부 서비스;     │  OpenAI / Anthropic /│
            │  네이버 크롤러      │  transport 자유)   │  OpenRouter /        │
            │                     │                    │  CodexOAuth (exp)    │
            └─────────────────────┴────────────────────┴──────────────────────┘
```

모든 구성요소는 단일 `docker-compose.yml`로 기동한다. Electron 앱만 호스트에서 `npm start`로 실행 (시연 모드).

## 컴포넌트 책임

### auth / consent / user (FastAPI 모듈)
사용자 회원가입, 로그인, 로그아웃, 토큰 갱신, 비밀번호 정책 검증, JWT 발급/폐기, 동의 상태 관리(UserConsent), 계정 삭제 요청. FR-01~06, FR-11, NFR-15~22를 충족. bcrypt(cost=12)와 JWT Access(15분) + Refresh(Redis 14일)을 표준으로 사용.

### topic-engine (CSO graph)
서비스 시작 시 PostgreSQL의 CSOTopic 테이블을 NetworkX `DiGraph`로 메모리 캐시 로드. 인접 토픽·상위 토픽·후손 토픽·동등 토픽 탐색을 그래프 거리 기반 단일 인터페이스로 제공. FR-13의 좌표계 역할.

### traversal-engine (사용자 × CSO trace)
`UserCSOTraversal` trace 객체 운영. 사용자 인터랙션을 받아 active trace에 매칭(extend) 또는 새 trace 생성, retract/split/archive 룰 기반 평가, leaf 재배치 LLM 호출. **사용자 관심 모델의 핵심**으로, 단일 노드가 아닌 path 자체가 추론 단위. user-level Redis lock으로 동시성 직렬화 ([`../sdd/concurrency.md §3`](concurrency.md)). 자세히는 [`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md).

### collection-orchestrator (v13 라운드 pivot, 2026-05-11)
사용자별 일일 수집 작업을 스케줄링·디스패치. **v13 pivot**: 6 어댑터 폐기 → `LLMProvider.search_with_tools()` 호출로 통일. 사용자 active trace JSON 을 LLM 에 입력 → LLM 이 web 검색 도구로 자료 fetch → Document 테이블 INSERT (source_id = sentinel `llm_search`, publisher 정보는 `Document.raw` JSONB). URL/DOI/제목 정규화 기반 dedup. FR-21~29 (v13 라운드 해석: [`../decisions.md §10`](../decisions.md)).

### leaf-lifecycle (LLM)
`LifecycleEvaluator` 추상 인터페이스의 D 하이브리드 구현체. 신규 동적 리프 식별과 병합 평가만 LLM 호출. emerging/active/stale/archived 상태 전이는 `topic_lifecycle.toml` 임계 룰. FR-14~16. 자세한 알고리즘은 [`../algorithms/leaf-topic-lifecycle.md`](../algorithms/leaf-topic-lifecycle.md).

### interest-bayesian
Beta-Bernoulli 사후 업데이트. UserEvent 입력 → `event_weights.toml` 가중치 → 단/장기 두 관측창 사후 갱신. UserInterestState에 long_score / short_score 저장. FR-17~20. 알고리즘은 [`../algorithms/interest-bayesian.md`](../algorithms/interest-bayesian.md).

### recommendation-engine
core/adjacent/discovery 후보 생성 + 신뢰도 임계 + fallback. Cold-start는 LLM이 직접 10개 추천 JSON 생성. 일반 추천은 (관심 적합도 × 신선도 × 소스 신뢰도) 점수로 랭킹. FR-26, FR-35~45. 알고리즘은 [`../algorithms/recommendation-ranking.md`](../algorithms/recommendation-ranking.md).

### Postgres 16 + pgcron + NetworkX 캐시
21개 엔티티의 영구 저장. JSONB로 토픽 메타·LLM 응답 흡수. NetworkX 캐시는 in-process. pgcron 또는 별도 워커가 일일 수집·라이프사이클 잡 트리거. 자세한 스키마는 [`../data/schema.md`](../data/schema.md).

### Redis 7
1) JWT Refresh 토큰 메타 (`refresh:{user_id}:{jti}`), 2) slowapi rate limit 카운터, 3) RQ/APScheduler 큐, 4) 추천 캐시 (사용자별 최신 추천 JSON, NFR-12 p95 3초 충족용).

### Workers (RQ)
일일 수집·라이프사이클 평가·병합 평가·요약 생성 작업을 비동기 처리. FastAPI와 동일 컨테이너 이미지 + ENTRYPOINT만 다름.

### ~~Source Adapters~~ (v13 라운드 폐기, 2026-05-11)
~~어댑터는 공통 `SourceAdapter` 인터페이스(`fetch(topic_query, since) -> List[RawDocument]`)를 만족. 6 종 구현체.~~

A4 Topic-driven Pivot 으로 폐기. `app/source_adapters/` 디렉토리 미생성. 수집은 `LLMProvider.search_with_tools()` 단일 경로.

### Clickbait DoRA (외부 서비스, vLLM 기반; transport-agnostic 계약) — v13 라운드 발동 조건 변경
사용자 보유 DoRA 파인튜닝된 `A.X-4.0-Light` 모듈 wrapper. **(v13 라운드, 2026-05-11)**: 1차 시연 default 비활성. 사용자가 News 소스 명시 활성화 시만 LLM 검색 응답에 post-filter 로 호출. 모듈 위치 = `clickbait_module/`, 서빙 엔진 = vLLM (DoRA를 base에 사전 머지 후 일반 base로 로드 + continuous batching). 호스팅·transport는 운영 결정으로 backend는 `CLICKBAIT_SERVICE_URL` env로만 호출. 입출력 계약은 [`../algorithms/clickbait-integration.md`](../algorithms/clickbait-integration.md).

### LLM Adapter (llm-adapter)
`LLMProvider` 추상 인터페이스 + 5 구현체. 기본은 **`MockProvider`** (deterministic fixture per prompt hash) — 누구나 클론 즉시 부트되고 CI/시연 안정성을 보장. 정식 호출용으로 `OpenAIAPIProvider`, `AnthropicAPIProvider`, `OpenRouterProvider`. 로컬 실험용으로 `CodexOAuthProvider` (openclaw/hermes 방식의 비공식 OAuth 세션 — **본인 토이 빌드 전용, 배포·시연 환경 기본값 아님**). 환경변수 `LLM_PROVIDER`로 토글. 모델 슬롯은 `LLM_MODEL_HIGH` (동적 리프 생성·병합), `LLM_MODEL_MEDIUM` (요약·추천 이유). 호출은 retry + token budget guard.

## 외부 인터페이스 (SRS §2.1.1) 매핑

| 외부 대상 | 책임 모듈 |
|---|---|
| 이메일 계정 인증 시스템 | auth (FastAPI) |
| CSO 데이터 | topic-engine (cso-import.md 워크플로 + NetworkX 캐시) |
| 학술지/논문 데이터 소스 | Source Adapters (arXiv / OpenAlex / Semantic Scholar / DBLP) |
| 빅테크 공식 채널 | Source Adapters (RSS) |
| 테크 뉴스 소스 | Source Adapters (RSS / 네이버 BS4) → Clickbait DoRA 통과 필수 |
| 낚시성 탐지 모듈 | Clickbait DoRA |
| 관리자 웹 콘솔 | Next.js Admin Console + admin API |
