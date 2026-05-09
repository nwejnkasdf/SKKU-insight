# SRS §3.5–3.8. Constraints, Standards, Architecture, Data Flow

본 파일은 SRS v0.3 §3.5 (Design Constraints) ~ §3.8 (Data Flow Diagram)을 분할한 것이다. 다른 분할 파일: [`04-data-model.md`](04-data-model.md), [`06-evolution.md`](06-evolution.md). 구체 아키텍처 다이어그램과 컴포넌트 책임은 [`../sdd/architecture.md`](../sdd/architecture.md), 시퀀스 다이어그램은 [`../sdd/data-flow.md`](../sdd/data-flow.md) 참고.

> 📌 **이미지 자산 안내**: 아래 `../../assets/figure_03_architecture.png`, `../../assets/figure_04_context.png`, `../../assets/figure_05_dfd_level1.png` 마크다운 링크는 IEEE 830 원형 보존 목적으로 유지하지만, **본 저장소에 PNG 파일은 동봉되어 있지 않다**. 동등한 다이어그램은 다음 위치에서 Mermaid로 제공된다:
> - System Architecture (Figure 3) → [`../sdd/architecture.md`](../sdd/architecture.md) 의 ASCII + 컴포넌트 책임 단락
> - Level 0/1 Data Flow (Figures 4, 5) → [`../sdd/data-flow.md`](../sdd/data-flow.md) 의 Mermaid sequence diagram 5종

## 3.5 Design Constraints

- 클라이언트는 Windows 데스크톱 앱을 우선 대상으로 한다.
- 서버는 클라우드 기반으로 사용자 관심 상태와 추천 데이터를 저장한다.
- CSO는 전체 리프까지 강제하지 않고 상위 토픽 좌표계로 사용한다.
- 동적 리프 토픽은 사용자별 에이전트가 생성 또는 연결하되 하나 이상의 상위 CSO 토픽과 연결되어야 한다.
- 초기 추천은 대시보드 10개 카드로 고정한다.
- 초기 추천 슬롯 비율은 후보가 충분한 경우 5:3:2를 목표로 하며, 후보 부족 시 신뢰도 기준을 만족하는 fallback으로 총 10개를 유지한다.
- 낚시성 탐지 결과는 사용자에게 직접 점수로 노출하지 않는다.
- 사용자 행동 로그와 관심 상태는 사용자가 동의를 철회하거나 계정 및 개인화 데이터 삭제를 요청하기 전까지 보관한다.
- 관리자 운영 기능은 일반 사용자용 Windows 앱이 아니라 별도 내부 웹 콘솔에서 제공한다.

## 3.6 Standards Compliance

- SRS 문서 구조는 IEEE Std 830-1998의 Software Requirements Specification 구성을 참고한다.
- 사용자 데이터 처리는 개인정보 보호 원칙을 준수한다.
- 외부 문서 수집은 각 소스의 이용 정책을 준수해야 한다.

## 3.7 System Architecture

![Figure 3. System Architecture](../../assets/figure_03_architecture.png)

Figure 3은 Windows 데스크톱 앱, 인증 서비스, 관심 상태 서비스, 사용자별 토픽 매핑 서비스, 사용자별 소스 수집 에이전트, 낚시성 탐지 모듈, 추천 서비스, 클라우드 DB, 별도 관리자 웹 콘솔의 관계를 보여준다. 구체 컴포넌트 책임 분할(Electron, Next.js, FastAPI 모듈군, Postgres, Redis, Workers, Source Adapters, Clickbait DoRA, LLM Adapter)은 [`../sdd/architecture.md`](../sdd/architecture.md)에서 다룬다.

## 3.8 Data Flow Diagram

### 3.8.1 Level 0 Context Diagram

![Figure 4. Level 0 Context Diagram](../../assets/figure_04_context.png)

Figure 4는 사용자, 별도 관리자 웹 콘솔 운영자, 학술지/논문, 빅테크 공식 채널, 테크 뉴스가 SKKU InSight 시스템과 상호작용하는 외부 맥락을 보여준다.

### 3.8.2 Level 1 Data Flow Diagram

![Figure 5. Level 1 Data Flow Diagram](../../assets/figure_05_dfd_level1.png)

Figure 5는 동의/인증, 행동 로그, 관심 상태 추론, 사용자별 토픽 매핑, 사용자별 소스 수집, 낚시성 탐지, 추천 생성, 추천 저장소, 관리자 웹 콘솔로 이어지는 내부 데이터 흐름을 보여준다. Mermaid 시퀀스 버전은 [`../sdd/data-flow.md`](../sdd/data-flow.md) 참고.
