# SRS §3.3. Nonfunctional Requirements

본 파일은 SRS v0.3 §3.3 (NFR-01~26)을 분할한 것이다. 다른 분할 파일: [`02-functional-requirements.md`](02-functional-requirements.md), [`04-data-model.md`](04-data-model.md), [`08-acceptance-tests.md`](08-acceptance-tests.md).

본 비기능 요구사항 표(Table 6)는 사용성, 신뢰성·정확성, 효율성, 보안·프라이버시, 조직, 외부 요구사항으로 분류된다.

## 3.3.1 Product Requirements

### 3.3.1.1 Usability Requirements

| ID | 요구사항 |
|---|---|
| NFR-01 | 사용자는 앱 실행 후 대시보드에서 최신 추천을 즉시 확인할 수 있어야 한다. |
| NFR-02 | 추천 카드는 사용자가 소스 유형과 관련 토픽을 빠르게 파악할 수 있도록 표시되어야 한다. |
| NFR-03 | 추천 이유는 문서 상세 화면에서 짧은 토픽 근거 수준으로 제공되어야 한다. |
| NFR-04 | 시스템은 사용자 행동 로그나 관심 점수를 사용자 화면에 직접 노출하지 않아야 한다. |

> **A8-v2 라운드 정합 박스 (2026-05-19)** — A8-v2 본문 ([`../decisions.md §15`](../decisions.md)) 이 도입한 `UserProfile` (캐릭터 한 단락 + fusion candidates JSONB) 의 NFR-04 정합 정책:
> - **UserProfile 자체 비노출** — admin console 도, 일반 사용자 UI (UI-05 설정) 도 노출 안 함. ORM/schema 만 영속, endpoint 부재. 향후 노출 결정 시 endpoint 추가 (별도 SRS 결정 후).
> - **Discovery 카드 `reason_short` 만 노출** — fusion / reincarnation 카드 옆 한국어 한 줄 (≤80자). 시간·강도 추상화 표현 (예: "현재 관심과 과거 관심이 만나는 영역", "N주 전 강한 흥미 영역에서 이어집니다") 만 허용. raw 점수·확률·버킷·`score_tail` 키워드 자동 거부 (reasons.py 의 `_REJECTED_KEYWORDS`).
> - **Score 컬럼** (Recommendation.score, UserInterestState.long_score/short_score 등) 은 A8 § §11.#4 정책 그대로 — admin 노출만, 일반 사용자 응답 schema 부재.

### 3.3.1.2 Reliability and Accuracy Requirements

| ID | 요구사항 |
|---|---|
| NFR-05 | 추천 랭킹은 사용자 관심 토픽 적합도와 소스 신뢰도를 필수 입력으로 포함해야 한다. |
| NFR-06 | 시스템은 최종 랭킹에서 사용자 관심 토픽 적합도를 1순위 정렬 기준으로 사용해야 한다. |
| NFR-07 | 낚시성으로 판정된 2차 문서는 추천 후보에서 제외되어야 한다. |
| NFR-08 | 토픽 연결 오류와 사용자별 소스 수집 실패는 관리자 웹 콘솔에서 확인 가능해야 한다. |
| NFR-09 | 낚시성 탐지 모듈은 DoRA 파인튜닝된 `A.x 4.0 light` 기반이며, 별도 validation 기준 accuracy, recall, AUROC, F1이 모두 98%대인 모듈이어야 한다. |
| NFR-10 | 등록된 사용자별 일일 수집 작업 성공률은 95% 이상이어야 한다. |

### 3.3.1.3 Efficiency Requirements

| ID | 요구사항 |
|---|---|
| NFR-11 | 사용자별 기술 동향 수집은 초기 버전에서 일 1회 수행되어야 한다. |
| NFR-12 | 대시보드 조회 API는 캐시된 추천 결과 기준 p95 3초 이하로 응답해야 한다. |
| NFR-13 | 시스템은 초기 버전에서 정규 에이전트 수집을 사용자별 일 1회로 제한하고, 추가 처리는 추천 10개 보충 또는 실패 재실행에 필요한 경우로 한정해야 한다. |
| NFR-14 | Windows 앱 로컬 캐시는 기본 설정에서 1GB를 초과하지 않아야 한다. |

### 3.3.1.4 Security and Privacy Requirements

| ID | 요구사항 |
|---|---|
| NFR-15 | 시스템은 사용자의 이메일 계정 정보를 서버 DB 접근 제어와 전송 구간 암호화를 통해 보호해야 한다. |
| NFR-16 | 비밀번호는 솔트가 적용된 단방향 해시로 저장해야 한다. |
| NFR-17 | 인증 토큰은 발급 시 만료 시간을 포함해야 하며, 만료된 토큰으로 API를 호출하면 인증 실패로 처리해야 한다. |
| NFR-18 | 시스템은 개인정보 및 행동 로그 수집 목적을 온보딩에서 안내해야 한다. |
| NFR-19 | 시스템은 사용자의 동의 없이 회원가입 또는 초기 설정을 완료하지 않아야 한다. |
| NFR-20 | 클라이언트와 서버 간 통신은 HTTPS를 사용해야 한다. |
| NFR-21 | 시스템은 사용자 계정 삭제 요청 접수 즉시 신규 개인화 처리를 중단하고, 계정, 행동 로그, 관심 상태, 저장/숨김 기록을 30일 이내 삭제해야 한다. |
| NFR-22 | 관리자 웹 콘솔은 일반 사용자와 분리된 관리자 권한으로만 접근 가능해야 한다. |

## 3.3.2 Organizational Requirements

| ID | 요구사항 |
|---|---|
| NFR-23 | SRS와 이후 설계 문서는 소프트웨어공학개론 조별과제 산출물 형식에 맞춰 작성되어야 한다. |
| NFR-24 | 시스템은 Windows 앱을 1차 대상으로 하며, Mac 지원은 시스템 진화 항목에서 다룬다. |

## 3.3.3 External Requirements

| ID | 요구사항 |
|---|---|
| NFR-25 | 시스템은 외부 소스의 원문 전체를 무단 복제하지 않고 제목, 링크, 요약, 메타데이터 중심으로 저장해야 한다. |
| NFR-26 | 시스템은 사용자 데이터 수집 목적, 저장 항목, 삭제 방법을 온보딩 및 설정 화면에서 안내해야 한다. |

> **v13 라운드 NFR-25 정합 박스 (2026-05-11)** — A4 Topic-driven Pivot ([`decisions.md §10`](../decisions.md))으로 LLM tool-use 검색 모델 채택. LLM 검색 응답에 외부 원문 abstract 가 포함될 가능성을 다음 정책으로 차단:
> - LLM 검색 prompt 에 instruction 포함: "각 결과의 abstract 는 원본 그대로 복사하지 말고, 본인 말로 1~2문장 요약하라 (≤200자)".
> - `Document.summary` 컬럼 (schema.md ORM) 에 저장되는 값은 **LLM self-summary** 이며 외부 원문 직접 복제가 아님.
> - publisher 정보 (domain·label) 는 `Document.raw` JSONB ([`schema.md` Document 섹션](../data/schema.md)) 에 metadata 로만 저장.
> - URL 은 그대로 저장 (외부 원본 접근 경로 — 사용자가 클릭 시 fresh fetch).
> - 본 정책으로 NFR-25 정합 유지. 추가 LLM 호출 (rewording) 불필요.
