# SRS §1. Introduction

본 파일은 SRS v0.3 §1 (Purpose / Scope / Definitions / References / Overview)을 분할한 것이다. 원본은 `/Users/hyojung/학교 과제/소프트웨어공학개론/SKKU_InSight_SRS.md`. 다른 분할 파일: [`01-overall-description.md`](01-overall-description.md), [`02-functional-requirements.md`](02-functional-requirements.md), [`03-nonfunctional-requirements.md`](03-nonfunctional-requirements.md), [`04-data-model.md`](04-data-model.md), [`05-architecture-and-flows.md`](05-architecture-and-flows.md), [`06-evolution.md`](06-evolution.md), [`07-traceability.md`](07-traceability.md), [`08-acceptance-tests.md`](08-acceptance-tests.md), [`09-open-issues-resolved.md`](09-open-issues-resolved.md).

## 문서 메타

- 문서명: SKKU InSight 소프트웨어 요구사항 명세서
- 버전: 0.3
- 작성일: 2026-05-04
- 작성 대상: 소프트웨어공학개론 조별과제
- 문서 형식: Markdown 원본 문서

### 문서 이력

| 버전 | 날짜 | 변경 내용 |
|---|---:|---|
| 0.1 | 2026-05-03 | SRS 초안 작성 |
| 0.2 | 2026-05-04 | Use Case, 소스 정책, 낚시성 탐지 모듈, 데이터 요구사항, 다이어그램 및 와이어프레임 보강 |
| 0.3 | 2026-05-04 | 추천 fallback, 동의 철회, 사용자별 수집, 사용자별 리프 토픽, 관리자 웹 콘솔, 검증 시나리오 보강 |

### List of Figures

| 번호 | 제목 |
|---|---|
| Figure 1 | Use Case Diagram |
| Figure 2 | Entity Relationship Diagram |
| Figure 3 | System Architecture |
| Figure 4 | Level 0 Context Diagram |
| Figure 5 | Level 1 Data Flow Diagram |
| Wireframe 1 | Onboarding |
| Wireframe 2 | Dashboard |
| Wireframe 3 | Topic Detail |
| Wireframe 4 | Document Detail |
| Wireframe 5 | Settings & Feedback |
| Wireframe 6 | Admin Web Console |

### List of Tables

| 번호 | 제목 |
|---|---|
| Table 1 | Acronyms and Abbreviations |
| Table 2 | Terms and Definitions |
| Table 3 | User Classes and Characteristics |
| Table 4 | User Interface Requirements |
| Table 5 | Functional Requirements |
| Table 6 | Nonfunctional Requirements |
| Table 7 | Data Dictionary |
| Table 8 | Requirements Traceability Matrix |
| Table 9 | Acceptance Test Scenarios |

## 1.1 Purpose

이 문서는 `SKKU InSight`의 소프트웨어 요구사항을 정의한다. `SKKU InSight`는 이공계 학생, 연구자, 교수가 직접 정보를 검색하지 않아도 자신의 관심사에 맞는 CS/AI 기술 동향을 선제적으로 제공받을 수 있도록 돕는 Windows 데스크톱 애플리케이션이다.

본 문서는 서비스의 목적, 범위, 사용자 특성, 기능 요구사항, 외부 인터페이스 요구사항, 비기능 요구사항, 데이터 요구사항, 제약사항 및 향후 확장 방향을 명세한다. 이후 설계 명세서, 테스트 명세서, 구현 계획의 기준 문서로 사용된다.

## 1.2 Scope

`SKKU InSight`는 사용자의 관심 상태를 추론하고, 해당 관심 상태에 맞는 기술 동향 자료를 수집, 정제, 추천하는 개인화 기술 동향 서비스이다.

1차 구현 대상은 일반 사용자용 Windows 데스크톱 앱이다. 사용자는 일반 이메일과 비밀번호로 가입하고 로그인한 뒤, 온보딩에서 넓은 관심 분야를 선택한다. 시스템은 선택된 관심 분야를 CSO 기반 상위 토픽으로 매핑하고, 이후 사용자의 열람 행동을 바탕으로 장기/단기 관심 상태를 지속적으로 갱신한다.

초기 서비스 도메인은 CS/AI 분야로 한정한다. 기본 소스는 학술지/논문, 빅테크 기업의 공식 발표 및 공식 채널, 테크 관련 뉴스로 구성한다. 향후 시스템 진화 단계에서 Mac 지원, 메일 digest 제공, CS/AI 외 이공계 도메인 확장을 고려한다.

본 SRS의 주요 범위는 다음과 같다.

- Windows 데스크톱 앱 기반 사용자 기능
- 일반 이메일 및 비밀번호 기반 회원가입과 로그인
- 개인정보 및 개인화 데이터 수집 동의
- 온보딩 관심 분야 선택
- CSO 상위 토픽과 사용자별 에이전트 기반 동적 리프 토픽을 활용한 관심 상태 추론
- 사용자별 관심 토픽에 맞춘 학술지/논문, 빅테크 공식 채널, 테크 뉴스 일일 수집
- DoRA 파인튜닝된 `A.x 4.0 light` 기반 낚시성 탐지 모듈을 통한 뉴스/기사 필터링
- `core`, `adjacent`, `discovery` 슬롯 기반 10개 추천 대시보드
- 토픽 상세, 문서 상세, 설정/피드백 화면
- 운영 모니터링용 별도 관리자 웹 콘솔

본 SRS에서 실제 구현 코드는 다루지 않는다.

## 1.3 Definitions, Acronyms, and Abbreviations

### 1.3.1 Acronyms and Abbreviations (Table 1)

| 약어 | 설명 |
|---|---|
| AI | Artificial Intelligence |
| API | Application Programming Interface |
| CS | Computer Science |
| CSO | Computer Science Ontology |
| DB | Database |
| DFD | Data Flow Diagram |
| DoRA | Weight-Decomposed Low-Rank Adaptation |
| ERD | Entity Relationship Diagram |
| LLM | Large Language Model |
| SRS | Software Requirements Specification |
| UI | User Interface |

### 1.3.2 Terms and Definitions (Table 2)

| 용어 | 정의 |
|---|---|
| 사용자 | SKKU InSight를 사용하는 이공계 학생, 연구자, 교수 |
| 관리자 | 별도 내부 웹 콘솔에서 서비스 운영 상태를 확인하고 수집/분류/필터링 오류를 모니터링하는 운영 담당자 |
| 관심 상태 | 사용자가 특정 기술 토픽에 대해 보이는 장기/단기 관심의 추론 결과 |
| 상위 토픽 | CSO 기반으로 정의되는 비교적 넓고 안정적인 기술 주제 |
| 동적 리프 토픽 | 사용자별 에이전트가 수집 자료와 최신 동향을 바탕으로 생성하거나 연결하는 세부 기술 주제 |
| Core Match | 이미 강한 관심이 확인된 주제 기반 추천 |
| Adjacent Expansion | 현재 관심과 인접한 주제 기반 추천 |
| Discovery Probe | 아직 확신은 낮지만 잠재적으로 관심 있을 수 있는 새 주제 기반 추천 |
| 관리자 웹 콘솔 | 일반 사용자용 Windows 앱과 분리되어 운영자가 수집 상태, 실패 로그, 필터링 통계, 재실행 요청을 관리하는 내부 도구 |
| 학술 소스 | 학술지, 논문 데이터베이스, 학회 proceedings 등 연구 결과를 담은 원천 자료 |
| 빅테크 공식 채널 | 기업 공식 블로그, 공식 연구 블로그, 릴리즈 노트, 공식 문서, 컨퍼런스 발표 |
| 테크 뉴스 | 기술 동향을 전달하는 뉴스/기사성 문서 |
| 1차 소스 | 학술 소스와 빅테크 공식 채널처럼 원천성이 높은 자료 |
| 2차 문서 | 테크 뉴스처럼 원천 자료를 해석하거나 재가공한 자료 |
| 공식 벤더 블로그 | 기업이 직접 운영하는 공식 블로그 또는 연구 블로그 |
| 독립 기술 블로그 | 기업 공식 채널이 아닌 개인 또는 독립 매체의 기술 해설 글 |
| 낚시성 탐지 | 2차 문서가 과장된 제목, 낮은 정보 밀도, 클릭 유도성 표현을 포함하는지 판별하는 기능 |

## 1.4 References

- IEEE Std 830-1998, IEEE Recommended Practice for Software Requirements Specifications
- Computer Science Ontology, https://cso.kmi.open.ac.uk/
- AI Hub 낚시성 기사 탐지 데이터셋
- 7팀 예시 SRS: `Team7_SRS.pdf`
- 12팀 예시 SRS: `Requirements Specification_Team 12.pdf`

## 1.5 Overview

2장은 제품의 전체 관점, 주요 기능, 사용자 특성, 운영 환경, 제약사항 및 가정을 설명한다. 3장은 외부 인터페이스, 기능 요구사항, 상세 Use Case, 비기능 요구사항, 데이터 요구사항, 설계 제약, 표준 준수, 시스템 구조와 진화 방향을 상세히 명세한다. 4장은 요구사항 추적표, 인수 테스트 시나리오, 후속 설계에서 결정할 항목을 제공한다.
