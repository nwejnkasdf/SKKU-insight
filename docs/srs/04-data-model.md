# SRS §3.4. Logical Database Requirements

본 파일은 SRS v0.3 §3.4 (Data Dictionary + ERD)를 분할한 것이다. 다른 분할 파일: [`02-functional-requirements.md`](02-functional-requirements.md), [`05-architecture-and-flows.md`](05-architecture-and-flows.md). 구체 SQLAlchemy 모델 의사 코드는 [`../data/schema.md`](../data/schema.md), Mermaid ERD는 [`../data/erd.mmd`](../data/erd.mmd) 참고.

> 📌 **이미지 자산 안내**: 아래 `../../assets/figure_02_erd.png` 마크다운 링크는 IEEE 830 원형 보존 목적으로 유지하지만, **본 저장소에 PNG 파일은 동봉되어 있지 않다**. 동등한 ERD는 [`../data/erd.mmd`](../data/erd.mmd) 의 Mermaid 다이어그램으로 제공한다.

## 3.4.1 Data Dictionary (Table 7)

| 엔티티 | 주요 속성 | 설명 |
|---|---|---|
| User | user_id, email, password_hash, created_at, deleted_at | 사용자 계정 |
| AdminUser | admin_id, email, password_hash, role, status, created_at, last_login_at | 관리자 웹 콘솔 계정과 권한 |
| UserConsent | consent_id, user_id, consent_type, agreed_at, revoked_at | 개인정보 및 로그 수집 동의 |
| BroadInterest | broad_interest_id, name, description | 온보딩에서 선택 가능한 넓은 관심 분야 |
| CSOTopic | cso_topic_id, label, uri, parent_topic_id | CSO 기반 상위 토픽 |
| DynamicLeafTopic | leaf_topic_id, user_id, label, confidence, status, created_at, merged_into_leaf_topic_id | 사용자별 에이전트가 생성/연결한 세부 토픽 |
| DynamicLeafTopicCSOTopic | leaf_topic_id, cso_topic_id, confidence, linked_at | 동적 리프 토픽과 하나 이상의 상위 CSO 토픽을 연결하는 매핑 |
| UserInterestState | state_id, user_id, cso_topic_id, leaf_topic_id, long_score, short_score, updated_at | 사용자별 관심 상태 |
| SourcePolicy | policy_id, source_category, trust_level, collection_rule, enabled | 소스 유형별 수집 및 신뢰도 정책 |
| Source | source_id, name, source_type, url, trust_level | 수집 대상 소스 |
| CollectionJob | job_id, user_id, source_id, target_cso_topic_id, target_leaf_topic_id, job_type, status, failure_reason, retry_count, started_at, finished_at | 사용자별 수집 작업 상태와 실패 로그 |
| Document | document_id, source_id, title, normalized_title, url, canonical_url, doi, summary, published_at, content_type | 수집된 문서와 중복 제거용 식별 정보 |
| DocumentTopic | document_id, cso_topic_id, leaf_topic_id, confidence | 문서와 토픽 연결 |
| ClickbaitResult | result_id, document_id, model_name, adapter_type, decision, confidence, evaluated_at | 낚시성 탐지 결과 |
| RecommendationSlot | slot_id, user_id, slot_type, target_count, actual_count, fallback_reason | 추천 슬롯 구성 결과 |
| Recommendation | recommendation_id, user_id, document_id, slot_type, reason, created_at | 사용자별 추천 결과 |
| UserEvent | event_id, user_id, document_id, event_type, dwell_time, created_at | 사용자 행동 로그 |
| SavedDocument | user_id, document_id, saved_at | 사용자가 저장한 문서 |
| HiddenDocument | user_id, document_id, hidden_at | 사용자가 숨김 처리한 문서 |
| NotInterestedTopic | user_id, cso_topic_id, leaf_topic_id, created_at | 사용자가 관심 없음으로 표시한 토픽 |
| ReprocessRequest | request_id, admin_id, job_id, requested_at, status, result_message | 관리자 웹 콘솔에서 요청한 실패 작업 재실행 기록 |

> **A8-v2 라운드 정합 박스 (2026-05-19)** — SRS Table 7 의 식별자·표는 원형 보존하되, A8-v2 본문 ([`../decisions.md §15`](../decisions.md)) 으로 신규 entity `UserProfile` 추가. SRS 원형 보존 정책 (헌법 §3) 에 따라 본 표를 직접 수정하지 않고 [`../data/schema.md` UserProfile §](../data/schema.md) 가 본 entity 의 SOR. 본 entity 는 사용자별 1 row (PK=user_id, 1:1), daily 19 UTC LLM cron 이 archive × current cross-product 융합 + reincarnation seeds 를 생성·영속. 노출 정책: ORM/schema 만, endpoint·UI 부재 (향후 노출 결정 시 endpoint 추가). discovery slot 2 (Fusion + Reincarnation) 의 input SOR.

## 3.4.2 Entity Relationship Diagram

![Figure 2. Entity Relationship Diagram](../../assets/figure_02_erd.png)

Figure 2는 사용자, 사용자별 리프 토픽, CSO 토픽 매핑, 사용자별 수집 작업, 문서 중복 제거 정보, 낚시성 탐지 결과, 추천 슬롯, 관리자 재실행 요청 사이의 주요 관계를 보여준다. Mermaid 버전은 [`../data/erd.mmd`](../data/erd.mmd) 참고.
