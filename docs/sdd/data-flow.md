# 데이터 흐름 (시퀀스 다이어그램)

본 파일은 SKKU InSight 핵심 시나리오 5개의 시퀀스를 Mermaid로 정의한다. SRS §3.8 DFD의 시퀀스 보충본이다. 컴포넌트 책임은 [`architecture.md`](architecture.md), 모듈 인터페이스는 [`module-boundaries.md`](module-boundaries.md) 참고.

## 1. 신규 가입 → 온보딩 → 동의 → Cold-start (UC-01)

```mermaid
sequenceDiagram
    autonumber
    actor User as 사용자
    participant App as Electron App
    participant API as FastAPI (auth/consent/onboarding)
    participant DB as Postgres
    participant Topic as topic-engine
    participant LLM as LLM Adapter
    participant Reco as recommendation-engine

    User->>App: 이메일 + 비밀번호 입력
    App->>API: POST /auth/signup
    API->>API: bcrypt cost=12 해시
    API->>DB: INSERT User (active_day_counter=0)
    API-->>App: 201 + onboarding_required=true
    App->>User: 동의 화면 표시 (NFR-18, NFR-26)
    User->>App: 동의 체크
    App->>API: POST /consent (consent_type=personalization)
    API->>DB: INSERT UserConsent(agreed_at=now)
    API-->>App: 200
    App->>User: 12 CSO 클러스터 표시
    User->>App: 3개 클러스터 선택
    App->>API: POST /onboarding/interests (cso_cluster_ids, user_class transient)
    API->>API: single-flight Redis lock (concurrency.md §2)
    API->>DB: UserInterestState alpha_prior boost on selected clusters (14 active days TTL)
    API->>API: enqueue cold-start job (RQ) → return 202 + polling_url
    API-->>App: 202 + request_id + polling_url + estimated_seconds=8
    Note over App: 비동기 폴링 시작
    App->>API: GET /onboarding/cold-start-status/{request_id} (1초 간격)
    API-->>App: status="running", progress=40
    par worker가 비동기 처리
        Reco->>LLM: cold_start_prompt(selected_csos, user_class, locale)
        LLM-->>Reco: 10개 추천 후보 JSON
        Reco->>Reco: validate_cold_start (slot 분배, URL/DOI 검증)
        Reco->>DB: INSERT pseudo Document (source_id = Source[name=SentinelSource.COLD_START_PSEUDO_NAME].id) for unmatched
        Reco->>DB: INSERT Recommendation x 10 + RecommendationSlot
        Reco->>Cache: SET recommendation:{user_id}
    end
    App->>API: GET /onboarding/cold-start-status/{request_id}
    API-->>App: status="completed", dashboard_ready=true
    App->>User: 대시보드 화면 전환
```

## 2. 일일 수집 잡 → 낚시성 필터 → 토픽 매핑 → emerging 식별 → active 승격 (UC-04 + FR-14·15)

```mermaid
sequenceDiagram
    autonumber
    participant Cron as Scheduler (RQ)
    participant Coll as collection-orchestrator
    participant LLMS as LLM Provider (search_with_tools)
    participant CB as Clickbait DoRA (옵션 — News 활성화 시만)
    participant Topic as topic-engine
    participant Leaf as leaf-lifecycle (D 하이브리드)
    participant LLM as LLM Adapter
    participant DB as Postgres

    Cron->>Coll: trigger daily_collect_for(user_id) (deterministic jitter, concurrency.md §8)
    Coll->>DB: SELECT active UserCSOTraversal + 1-hop adjacent topics (cso-topic-traversal.md §6.1)
    Note over Coll: 수집 대상 = current 영역(active trace path 노드) ∪ adjacent 영역(1-hop). proactive는 글로벌 트렌드라 사용자별 fetch 불필요.
    Coll->>LLMS: search_with_tools(trace_json, leaf_label) for each active leaf (v13 라운드)
    LLMS-->>Coll: list[SearchResult] (LLM self-summary, NFR-25 정합)
    Coll->>Coll: dedup(DOI/canonical_url/URL/제목 Levenshtein ≥ 0.90)
    Coll->>DB: INSERT Document(source_id=llm_search sentinel, raw=publisher meta), CollectionJob(status=running)
    opt 사용자가 News 소스 명시 활성화 시 (v13 라운드 — default 비활성)
        Coll->>CB: classify(title, body, meta)
        CB-->>Coll: {decision, confidence}
        alt decision == clickbait
            Coll->>DB: INSERT ClickbaitResult(decision=clickbait)
            Note over Coll: 추천 후보 제외 (FR-31)
        else decision == clean
            Coll->>DB: INSERT ClickbaitResult(decision=clean)
        end
    end
    Coll->>Topic: map_document_to_csos(doc)
    Topic-->>Coll: cso_topic_ids[]
    Coll->>DB: INSERT DocumentTopic
    Coll->>Leaf: identify_emerging(user_id, new_docs)
    Note over Leaf: active trace path 끝 산하에서만 분기 (cso-topic-traversal.md §1.3)
    Leaf->>LLM: prompt_xhigh(documents, existing_leaves, active_traces)
    LLM-->>Leaf: new_leaf_candidates JSON
    Leaf->>DB: INSERT DynamicLeafTopic(status=emerging) + DynamicLeafTopicCSOTopic
    Leaf->>Leaf: rule_evaluate(all leaves) — active day 차이 기반
    alt emerging가 7 active days 내 5건+ 관심신호 2건+
        Leaf->>DB: UPDATE DynamicLeafTopic SET status=active
    end
    Coll->>DB: UPDATE CollectionJob(status=succeeded)
```

## 3. 사용자 대시보드 조회 (UC-02)

```mermaid
sequenceDiagram
    autonumber
    actor User as 사용자
    participant App as Electron App
    participant API as FastAPI
    participant Reco as recommendation-engine
    participant Cache as Redis (recommendation cache)
    participant DB as Postgres

    User->>App: 앱 실행
    App->>API: GET /recommendations/dashboard (Bearer access_token)
    API->>API: verify_jwt + check_consent_active
    alt 동의 철회 상태
        API-->>App: 403 + reauth_required=true (FR-59)
        App->>User: 재동의/계정삭제 화면 (UI-05)
    else 동의 유효
        API->>Cache: GET recommendation:{user_id}
        alt cache hit (TTL = collection_cron 다음 실행 직전까지)
            Cache-->>API: 10개 카드 JSON
        else cache miss
            API->>Reco: build_dashboard(user_id)
            Reco->>DB: SELECT UserInterestState, Recommendation candidates
            Reco->>Reco: rank + slot fill (5/3/2)
            alt 슬롯 후보 부족
                Reco->>Reco: fallback per FR-42 (slot 대체)
                Reco->>DB: INSERT RecommendationSlot(fallback_reason=...)
            end
            alt 전체 후보 < 10
                Reco->>Reco: fallback per FR-43 (인접/트렌드 보충)
            end
            Reco-->>API: 10개 카드 + RecommendationSlot
            API->>Cache: SET recommendation:{user_id}
        end
        API-->>App: 200 + cards[]
        App->>User: 대시보드 표시 (UI-02)
    end
```

## 4. 추천 카드 클릭·저장·숨김·관심없음 → 베이지안 업데이트 (UC-03 + FR-17·19·20)

```mermaid
sequenceDiagram
    autonumber
    actor User as 사용자
    participant App as Electron App
    participant API as FastAPI
    participant Bayes as interest-bayesian
    participant Traversal as TraversalEngine
    participant DB as Postgres
    participant Cache as Redis

    User->>App: 카드 클릭
    App->>API: POST /events {event_type=click, document_id, dwell_ms, client_request_id}
    API->>API: redis user-lock (concurrency.md §3) + maybe_increment_active_day
    API->>DB: INSERT UserEvent (idempotent by client_request_id) — 또는 batch buffer (concurrency.md §6)
    API->>Bayes: ingest_event(event) — atomic SQL UPDATE (concurrency.md §4.1)
    Bayes->>DB: SELECT DocumentTopic WHERE document_id
    Bayes->>Bayes: weight × topic_distribution per topic
    Bayes->>DB: UPSERT UserInterestState SET alpha = alpha + delta (atomic)
    Bayes->>Bayes: propagate to active trace ancestors (1-hop 0.5 decay, cso-topic-traversal.md §4)
    API->>Traversal: ingest_event(user, event) — extend / split 트리거 검토
    Traversal->>DB: trace operation (룰 기반)
    API-->>App: 200
    User->>App: 저장 버튼
    App->>API: POST /events {event_type=save}
    API->>DB: INSERT UserEvent + SavedDocument
    API->>Bayes: ingest_event(weight=+5)
    Bayes->>DB: UPDATE UserInterestState
    User->>App: 숨김 버튼
    App->>API: POST /events {event_type=hide}
    API->>DB: INSERT UserEvent + HiddenDocument
    API->>Bayes: ingest_event(weight=-3)
    User->>App: 관심없음 버튼
    App->>API: POST /events {event_type=not_interested}
    API->>DB: INSERT UserEvent + NotInterestedTopic
    API->>Bayes: ingest_event(weight=-5)
    Bayes->>DB: UPDATE UserInterestState
    API->>Cache: DEL recommendation:{user_id}
    Note over Cache: 다음 GET /dashboard에서 재계산
```

## 5. 관리자 콘솔에서 실패 작업 재실행 (UC-05)

```mermaid
sequenceDiagram
    autonumber
    actor Admin as 관리자
    participant Web as Next.js Admin Console
    participant API as FastAPI (admin)
    participant DB as Postgres
    participant Q as RQ Queue

    Admin->>Web: 로그인 (admin@example.com)
    Web->>API: POST /admin/auth/login
    API->>API: verify bcrypt + AdminUser.role check (FR-60)
    API->>DB: SELECT AdminUser
    API-->>Web: 200 + admin_jwt
    Admin->>Web: 수집 상태 화면
    Web->>API: GET /admin/collection/jobs?status=failed
    API->>DB: SELECT CollectionJob WHERE status=failed
    API-->>Web: 실패 작업 목록
    Admin->>Web: 작업 선택 + 재실행
    Web->>API: POST /admin/collection/jobs/{id}/reprocess
    API->>API: assert admin_jwt.aud == "admin" (FR-60, NFR-22)
    API->>DB: INSERT ReprocessRequest(admin_id, job_id, status=queued)
    API->>Q: enqueue collection_job(id)
    API-->>Web: 202 + reprocess_request_id
    Q->>Q: 작업 실행
    Q->>DB: UPDATE CollectionJob(status=succeeded|failed) + UPDATE ReprocessRequest(status, result_message)
    Web->>API: GET /admin/reprocess-requests/{id}
    API-->>Web: 결과 조회
```
