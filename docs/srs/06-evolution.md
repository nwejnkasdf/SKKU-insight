# SRS §3.9. System Evolution

본 파일은 SRS v0.3 §3.9 (System Evolution + Change Limitation/Assumption + Change of User Requirements + Accommodating Changes)를 분할한 것이다. 다른 분할 파일: [`05-architecture-and-flows.md`](05-architecture-and-flows.md), [`07-traceability.md`](07-traceability.md).

## 3.9 System Evolution

초기 버전은 Windows 데스크톱 앱과 CS/AI 도메인 중심으로 구현한다. 이후 시스템은 다음 순서로 확장한다.

1. Mac 지원
2. 일간 또는 주간 메일 digest 제공
3. CS/AI 외 이공계 도메인 확장

### 3.9.1 Change Limitation and Assumption

- Mac 지원 시 클라우드 계정과 서버 DB 구조는 유지한다.
- 메일 digest는 기존 추천 결과를 다른 채널로 전달하는 기능으로 확장한다.
- 다중 도메인 확장 시 CSO 외 도메인별 온톨로지 또는 토픽 체계를 추가해야 한다.

### 3.9.2 Change of User Requirements

사용자가 더 많은 분야의 동향을 원하거나, 메일/알림 등 앱 외부 채널을 요구할 수 있다. 시스템은 관심 상태와 추천 결과를 플랫폼 독립적인 서버 데이터로 관리하여 플랫폼 확장과 채널 확장을 지원해야 한다.

### 3.9.3 To Accommodate the Changes Easily

| ID | 요구사항 |
|---|---|
| EV-01 | 시스템은 Windows 앱과 Mac 앱이 동일한 클라우드 계정 및 추천 API를 사용할 수 있도록 클라이언트와 서버 책임을 분리해야 한다. |
| EV-02 | 시스템은 메일 digest 기능이 기존 추천 결과와 문서 요약을 재사용할 수 있도록 추천 저장소를 채널 독립적으로 관리해야 한다. |
| EV-03 | 시스템은 CS/AI 외 도메인 확장 시 도메인별 온톨로지 또는 토픽 체계를 추가할 수 있도록 토픽 매핑 계층을 분리해야 한다. |

## 본 결정과의 매핑

| EV | 본 구현 결정에서의 반영 |
|---|---|
| EV-01 | Electron + React + TypeScript 채택으로 Mac 빌드 거의 무공수 (`docs/sdd/tech-stack.md`) |
| EV-02 | Recommendation 테이블이 채널 독립적 (`docs/data/schema.md`). 메일 digest 워커는 후속 포트폴리오 단계에서 추가 |
| EV-03 | TopicMapper 추상 인터페이스 → CSOTopicMapper 구현체 1차 제공. 다른 도메인은 새 매퍼만 추가 (`docs/sdd/module-boundaries.md`) |
