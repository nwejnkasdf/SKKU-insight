# SRS §4.3. Open Issues — 해결 상태

본 파일은 SRS v0.3 §4.3 (Open Issues 4건)을 분할하고, 구현 계획 v1.0 (`/Users/hyojung/.claude/plans/iridescent-swimming-stardust.md`) §1.4·§1.6에서 어떻게 해결됐는지 매핑한 것이다.

## 4.3 Open Issues — 원본 + 인터뷰 신규 식별

다음 항목은 설계 명세서 또는 후속 요구사항 회의에서 추가 결정한다.

1. 구체적인 관심 상태 점수 산식 (SRS 원본)
2. 시간 감쇠의 반감기와 이벤트별 가중치 (SRS 원본)
3. 동적 리프 토픽 병합/폐기 세부 조건 (SRS 원본)
4. Windows 데스크톱 앱 구현 프레임워크 (SRS 원본)
5. **사용자 × CSO 토픽 상태 머신·전이 룰** (SRS 원본 외 — 본 시스템 인터뷰에서 신규 식별)

> ※ 항목 5는 SRS v0.3에는 명시되지 않았으나, 설계 인터뷰에서 "CSO 토픽 자체에 사용자별 상태가 정의되지 않았다"는 갭이 식별되어 본 문서에 추가한다. 해당 결정은 [`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md) 에 반영된다.

## 해결 매핑

### 1. 구체적인 관심 상태 점수 산식

- **결정**: 베이지안 (Beta-Bernoulli) 모델 채택. 사용자별·토픽별 관심 사후를 단/장기 두 관측창으로 분리.
- **근거**: 점수 해석 가능성 + 콜드스타트 우호성. 토픽이 늘어도 모델 학습 비용이 0에 가까움.
- **반영 문서**: [`../algorithms/interest-bayesian.md`](../algorithms/interest-bayesian.md), [`../decisions.md`](../decisions.md) §4
- **튜닝**: `interest_params.toml` 파일로 노출 (alpha_prior, beta_prior, decay_short, decay_long)

### 2. 시간 감쇠의 반감기와 이벤트별 가중치

- **결정**: 두 관측창의 감쇠율을 분리해서 명시. **단기 t1/2 = 7 active days, 장기 t1/2 = 60 active days**를 초기값으로 (active day = 사용자 인터랙션 1+건 있는 날의 단조증가 카운터, [`../algorithms/cso-topic-traversal.md §5`](../algorithms/cso-topic-traversal.md)). 이벤트 가중치는 클릭+1 / 체류≥2분+2 / 저장+5 / 숨김−3 / 관심없음−5.
- **근거**: SRS FR-20 + 사용자 의도 강도 차이 반영. 명시 부정 신호(숨김/관심없음)를 명시 긍정 신호보다 강도 0.6배.
- **반영 문서**: [`../algorithms/interest-bayesian.md`](../algorithms/interest-bayesian.md)
- **튜닝**: `event_weights.toml`, `interest_params.toml`

### 3. 동적 리프 토픽 병합/폐기 세부 조건

- **결정**: D 하이브리드 (신규 식별·병합만 LLM, 승격·강등은 룰). `LifecycleEvaluator` 추상 인터페이스로 추후 B 배치 평가도 갈아끼울 수 있게 유지.
- **임계값(초기, 모두 active day 단위)**:
  - emerging → active: 7 active days 내 5건 이상 문서 수집 + 관심 신호 ≥ 2건
  - active → stale: 21 active days 신규 문서 0건 또는 관심 신호 0건
  - stale → archived: 90 active days 변화 없음
  - merged 평가: 주 1회 LLM 평가 (라벨 유사도 + 문서 집합 Jaccard ≥ 0.6) — 단 cron은 wallclock 기반
- **반영 문서**: [`../algorithms/leaf-topic-lifecycle.md`](../algorithms/leaf-topic-lifecycle.md)
- **튜닝**: `topic_lifecycle.toml`

### 4. Windows 데스크톱 앱 구현 프레임워크

- **결정**: Electron + React + TypeScript.
- **근거**: 풍부한 라이브러리 + Mac 확장 시 (EV-01) 동일 코드 재사용 + 한국어 i18n과 다크 테마 등 디자인 자유도.
- **반영 문서**: [`../sdd/tech-stack.md`](../sdd/tech-stack.md), [`../decisions.md`](../decisions.md) §2
- **토큰 보관**: Electron `safeStorage` API (OS 키체인)

### 5. 사용자 × CSO 토픽 상태 머신·전이 룰 (인터뷰 신규)

- **식별 배경**: SRS는 동적 리프 토픽의 4상태(emerging/active/stale/merged)는 정의했으나, **상위 좌표계인 CSO 토픽에 대해서는 사용자별 상태나 전이 룰이 정의되어 있지 않았다**. UserInterestState는 점수만 가지고 있어 "어떤 cso_topic이 활성인지", "행동 신호가 식으면 어떻게 되는지", "온보딩 선택의 시간축은 어떻게 다루는지" 등이 모두 비어 있었다.
- **결정**: **사용자 관심 = CSO 그래프 위 traversal trace 객체**. 사용자가 historical하게 흘러간 path 자체가 하나의 관심 상태 단위이며, 단일 노드가 아니다. 한 사용자에 multiple trace 분기 무제한 허용.
- **모델 코어**:
  - 행동이 root, 명시 선택은 14 active day 한정 prior boost
  - Trace operation: extend / retract / split / archive (룰 기반, leaf 재배치만 LLM)
  - current/adjacent/proactive ↔ core/adjacent/discovery 1:1 매핑
  - dynamic leaf는 active trace path 위 노드 산하에서만 분기
  - 모든 N일 임계는 active day(사용자 인터랙션 1+건 있는 날) 기준으로 통일
- **반영 문서**: [`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md) (신규), [`../data/schema.md`](../data/schema.md) (`UserCSOTraversal` 테이블 추가), [`../decisions.md`](../decisions.md) §4
- **튜닝**: `topic_lifecycle.toml`의 `[traversal]`, `[propagation]` 섹션
- **운영 결정 — `archived` 상태 추가**: SRS Data Dictionary는 동적 리프 4상태(emerging/active/stale/merged)만 명시했으나, 운영상 `archived` 상태(stale 누적 90 active day 후 자동)를 5번째 상태로 추가하기로 결정. SRS 식별자는 보존하되 schema/erd/lifecycle/traversal 모두 5상태로 일관 운영.
