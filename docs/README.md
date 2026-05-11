# SKKU InSight 문서 인덱스

`SKKU InSight`는 이공계 학생, 연구자, 교수가 직접 검색하지 않아도 자신의 관심 상태에 맞는 CS/AI 기술 동향을 선제적으로 제공받을 수 있도록 돕는 Windows 데스크톱 애플리케이션이다. 백엔드는 FastAPI, 데이터 저장은 PostgreSQL과 Redis, 클라이언트는 Electron + React + TypeScript, 운영용 관리자 콘솔은 Next.js로 구현하며 모든 구성요소는 단일 `docker-compose.yml`로 기동한다.

본 `docs/` 계층은 SRS(요구사항 명세) 분할본과 설계·구현 결정의 단일 진실 공급원 역할을 한다. 후속 코드 작성 에이전트는 자기 모듈에 해당하는 디렉토리만 컨텍스트로 받아 작업한다.

## 디렉토리별 목적

| 경로 | 목적 | 주된 독자 |
|---|---|---|
| `docs/decisions.md` | 13 라운드 결정 매트릭스 압축본 (v13 = A4 Topic-driven Pivot, 2026-05-11). 모든 코드 결정의 단일 진실 공급원 | 모든 에이전트 |
| `docs/decision-backlog.md` | 모든 `<!-- TODO: -->` 마커를 P0/P1/P2로 분류하고 default/stub 전략 명시 | 모든 에이전트 |
| `docs/srs/` | SRS v0.3 분할본. 원본 텍스트와 표 보존 | 요구사항 추적용 |
| `docs/sdd/` | 소프트웨어 설계 문서: 아키텍처, 데이터 흐름, 배포, 모듈 경계, 기술 스택, **동시성 가드**, **API 통신 규약** | A2~A10 |
| `docs/api/` | FastAPI 엔드포인트 명세 (auth / consent / **onboarding** / topics / interest / collection / recommendation / admin) | A2, A6, A8, A9, A10 |
| `docs/algorithms/` | 베이지안 관심 추론, **CSO 토픽 traversal trace** (사용자 관심 모델), 리프 토픽 라이프사이클, 추천 랭킹, 콜드스타트, 낚시성 통합, CSO 매핑 알고리즘 명세 | A3, A5, A6, A7, A8 |
| `docs/data/` | DB 스키마, ERD, 소스 레지스트리, CSO 임포트 워크플로, 시드 페르소나 | A2, A3, A4, A12 |
| `docs/ops/` | docker-compose 구성, 환경변수, CI/CD, 관리자 부트스트랩, 운영 런북 | A2, A11, 운영자 |
| `docs/security/` | 인증 흐름, 토큰 처리, 레이트 리밋, 비밀번호 정책, STRIDE 위협 모델 | A2, A11 |
| `docs/ux/` | 와이어프레임 인덱스, UI 상태 카탈로그, 한·영 정책, **Electron 클라이언트 동작 명세** | A9, A10 |

## 후속 에이전트 분할 (압축본)

상세는 [`decisions.md`](decisions.md) §에이전트 분할과 구현 계획 v1.0의 §4 참조.

### Phase 0 — Foundation (병렬 가능) — ✅ 모두 완료

- **A1. docs-bootstrap** ✅ — 본 디렉토리 작성 완료
- **A2. backend-foundation** ✅ — FastAPI 부트, docker-compose(pg+redis+api+worker), Alembic, 인증·동의·사용자 모듈 (FR-01~06, FR-11), 보안(NFR-15~22) — PR #4 + #7 머지
- **A3. cso-topic** ✅ — CSO 3.4 임포트, NetworkX 캐시, Topic·CSOTopic 테이블, 그래프 탐색 7 endpoint — 5 PR-stack (docs-drift + a2-orm-hotfix + a3-engine + 2 라운드 audit fix)

### Phase 1 — Data·정제 (Phase 0 후 병렬)
- **A4. collection** — **(v13 라운드 pivot, 2026-05-11)** `LLMProvider.search_with_tools()` 단일 경로 + Document/DocumentTopic/CollectionJob ORM + dedup + jitter + `/topics/{id}/documents` cross-cutting (FR-21~29 식별자 보존, v13 라운드 해석)
- **A5. clickbait** — 사용자 제공 DoRA 모듈 wrap, ClickbaitResult 저장 (FR-30~34). **(v13 라운드)** 1차 시연 default 비활성 — 사용자가 News 소스 명시 활성화 시만 호출
- **A6. interest-bayesian** — 행동 로그 API, Beta-Bernoulli 업데이트, 시간 감쇠, `interest_params.toml` (FR-12~20)

### Phase 2 — 추천 핵심 (Phase 1 후)
- **A7. leaf-lifecycle** — `LifecycleEvaluator` 추상 + D 하이브리드 구현, LLM 프롬프트, `topic_lifecycle.toml` (FR-14~16)
- **A8. recommendation** — core/adjacent/discovery 후보 생성, fallback, Cold-start LLM, 랭킹 (FR-35~45, FR-26)

### Phase 3 — UI (Phase 2 후 병렬)
- **A9. electron-client** — UI-01~05, API 연동, safeStorage 토큰 관리, 한국어 i18n
- **A10. admin-console** — UI-06 Next.js 콘솔, 수집 상태/실패/낚시성 통계/재실행 (FR-60~65)

### Phase 4 — 폴리시 + 데모
- **A11. test-ci** — pytest 단위·통합, vitest, AT 자동화, GitHub Actions
- **A12. demo-seed** — 5+명 페르소나 + 14일 인터랙션 + 일반·관리자 계정 자동 생성

### 모듈 의존
```
A1 ──(독립)──
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

## 핵심 결정 매트릭스 빠른 링크

- [전체 결정 매트릭스](decisions.md)
- [결정 백로그 (P0/P1/P2)](decision-backlog.md)
- [동시성 가드 (concurrency)](sdd/concurrency.md)
- [API 통신 규약 (api-conventions)](sdd/api-conventions.md)
- [Contracts SOR (enum·error code·Redis key)](sdd/contracts.md)
- [멀티 에이전트 오케스트레이션 (agent-orchestration)](sdd/agent-orchestration.md)
- [기술 스택 핀](sdd/tech-stack.md)
- [아키텍처 다이어그램](sdd/architecture.md)
- [모듈 경계와 인터페이스 계약](sdd/module-boundaries.md)
- [DB 스키마](data/schema.md)
- [SRS 추적표](srs/07-traceability.md)
- [Open Issue 해결 상태](srs/09-open-issues-resolved.md)

## 문서 작성 규칙

- 본문 한국어, 코드/CLI/식별자 영어
- SRS 용어(사용자, 관리자, 관심 상태, 동적 리프 토픽 등) 1.3 정의 그대로 사용
- FR-XX·NFR-XX·AT-XX·UC-XX 식 식별자는 SRS 그대로 인용
- Mermaid 코드 블록 펜스 사용
- 미해결 항목은 `<!-- TODO: ... -->` 마커로 표기
