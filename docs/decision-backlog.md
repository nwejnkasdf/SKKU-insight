# 결정 백로그 (Decision Backlog)

본 문서는 docs/ 전반에 흩어진 모든 `<!-- TODO: -->` / `# TODO:` 마커를 한 곳에 모아 우선순위(P0/P1/P2)를 부여하고, P0 외 항목은 reasonable default 또는 stub 전략을 제시한다. 후속 에이전트가 자기 모듈을 구현할 때 본 표를 따르면 막힘 없이 진행할 수 있다.

분류 기준:
- **P0 — 구현 시작 전 필수 결정**: 사용자 외부 자원·정책에 의존하므로 default가 불가능. 미해결 시 해당 에이전트가 막힘.
- **P1 — Default로 진행 가능**: 합리적 default를 코드/config에 박아 두고, 시연 후 튜닝.
- **P2 — 폴리시·검증 단계 항목**: 1차 구현 동안은 stub 또는 후순위. 본 시연에 영향 없음.

원본 마커 위치는 `file:line` 형태로 기록.

---

## P0 — 구현 시작 전 필수 결정 (0건, 모두 해소됨)

### P0-1. DoRA 낚시성 모듈 경로 공유 — **해결됨 (2026-05-11)**
- **원본**: `algorithms/clickbait-integration.md:9`, `sdd/architecture.md:74` (해소되어 마커 제거됨)
- **결정**: 모듈 위치 = `clickbait_module/`. 가중치 호스팅 = HuggingFace Hub private repo. 서빙 엔진 = **vLLM** (DoRA를 base에 사전 머지 후 일반 base로 로드 + continuous batching). 호스팅·transport는 운영 시점 결정 — backend는 `CLICKBAIT_SERVICE_URL` env로만 호출하며 transport·호스팅과 무관.
- **코드 phase 산출**: `clickbait_module/app/` (FastAPI + vLLM AsyncLLMEngine), `clickbait_module/scripts/merge_adapter.py` (peft `merge_and_unload()`). P1-8·P2-7도 본 결정으로 자연 해소(아래 참조).

## C-급 (이전 분석에서 식별, 인터뷰로 해소된 항목)

| ID | 원래 제기된 문제 | 해소 위치 |
|---|---|---|
| C-1. UserBroadInterest 매핑 누락 | onboarding 선택은 14 active day 한정 prior boost로 동작, 영구 저장 매핑 테이블 불필요로 결정 (행동이 root 모델). 영구 보존 필요 시 `algorithms/cso-topic-traversal.md §1.2` 참조 | `algorithms/cso-topic-traversal.md §1.2`, `decisions.md §4` |
| C-3. Cold-start pseudo Document FK 정합성 | 1차 시연에서는 Source 테이블에 sentinel 행 (`name="cold_start_pseudo"`) 시드 + content_type="pseudo_cold_start" 별도 처리. P1로 격하해 default 진행 | `algorithms/cold-start.md` (TODO 마커 유지) |
| C-4. emerging leaf 노출 경로 부재 | traversal trace 모델 도입으로 자연 해소: emerging은 active trace path 끝 산하에서만 분기되어 current 카테고리 = core 슬롯에 자연 포함. core 슬롯 5개 중 1개는 emerging quota | `algorithms/cso-topic-traversal.md §6.2`, `algorithms/recommendation-ranking.md` Core 섹션 |
| C-5. 사용자 × CSO 토픽 상태 머신·전이 룰 (인터뷰에서 신규 식별) | `UserCSOTraversal` trace 객체 도입 + active day 기반 라이프사이클로 해소. SRS Open Issue 5로 등록 | `algorithms/cso-topic-traversal.md` 전체, `decisions.md §4` |

> **C-2 (NFR-21 30일 grace period)**는 여전히 미해소. 1차 시연은 즉시 cascade로 진행하되, 시연 후 폴리시 단계에서 soft delete + worker 도입 검토. 운영 시 NFR-21 정합 주의.

---

## P1 — Reasonable Default로 진행 (8건, 활성 7 + 해결 1)

### P1-1. User 엔티티에 사용자 클래스(학생/연구자/교수) 필드 추가 여부
- **원본**: `algorithms/cold-start.md:15`
- **사용 맥락**: cold-start LLM 입력에 사용자 클래스를 힌트로 넣어 첫 10개 추천을 페르소나에 맞게 생성.
- **default**: **User 테이블에 추가하지 않는다.** 대신 온보딩 화면에서 받은 user_class를 transient input으로 cold-start LLM 호출 1회에만 사용하고 저장하지 않는다. 이후 일반 추천은 행동 로그(베이지안 사후)로 충분히 개인화된다.
- **stub**: `POST /onboarding/interests` ([`api/onboarding.md`](api/onboarding.md)) 요청 바디에 `user_class: "undergraduate" | "researcher" | "professor" | "general"` 필드를 받아 cold-start orchestrator로 transient 전달. User 영구 저장 없음.
- **튜닝 트리거**: 시연 후 사용자 클래스를 영구적으로 보고 싶다는 요구가 생기면 마이그레이션으로 컬럼 추가.

### P1-2. 24시간 cold-start LLM 호출 캡
- **원본**: `algorithms/cold-start.md:118`
- **default**: `recommendation.toml` 의 `cold_start_max_per_day = 100` (전역). 사용자당으로는 사실상 가입 직후 1회만 호출되므로 100/일 전역 캡으로 시연 충분.
- **stub**: 초과 시 fallback 경로 (trust_level=high 트렌드)로 자동 전환. 이미 `runbooks.md` §2 정의됨.

### P1-3. 낚시성 분류기 1회 추론 SLA
- **원본**: `algorithms/clickbait-integration.md:112`
- **default**: **CPU 환경 5초**, 초과 시 비동기 큐 전환. `clickbait-detector` 컨테이너의 `/classify` 엔드포인트는 동기 응답 5초 timeout, 초과 응답은 backend 측에서 RQ 큐로 wrap (`worker`가 후처리, 결과는 `ClickbaitResult.evaluated_at` 사후 채움).
- **stub**: 시연 환경(데이터 5+ 페르소나)에서는 모든 호출이 5초 이내 완료한다고 가정. 비동기 경로는 코드 골격만 두고 활성화는 시연 후.

### P1-4. UserEvent에서 leaf_topic 없는 이벤트의 토픽 분배 정책
- **원본**: `algorithms/interest-bayesian.md:184`
- **default**: **이벤트가 가리키는 Document에 매핑된 모든 (cso_topic, leaf_topic) 쌍의 confidence를 정규화해 분배.** leaf_topic이 없는 매핑은 cso_topic만 분배 대상에 포함. 사용자 명시 이벤트(`not_interested` 등)에서 토픽이 직접 지정된 경우는 100% 단일 분배.
- **stub**: `app/interest/topic_distribution.py` 의 `resolve_topic_distribution(event)` 함수가 위 룰을 구현.

### P1-5. CSO 다운로드 버전
- **원본**: `data/cso-import.md:227`
- **default**: **CSO 3.4** (현재 사이트의 안정 stable 다운로드). `make import-cso` 가 fetch URL을 환경변수 `CSO_DOWNLOAD_URL`에서 읽어 변경 가능.
- **stub**: `scripts/import_cso.py` 가 다운받은 N3/CSV의 hash를 `cso_metadata` 테이블에 기록 → 향후 버전 갱신 시 변경 감지.
- **튜닝 트리거**: 신버전(3.5+) 출시 시 `CSO_DOWNLOAD_URL`만 교체 후 `make import-cso --refresh`.

### P1-6. 네이버뉴스 Document 야간 정리 잡 정책
- **원본**: `data/schema.md:352`
- **default**: 매일 02:00 KST cron. 모든 토픽 매핑이 사라지고 `created_at` 으로부터 30일 경과한 `content_type=tech_news` Document는 삭제. SRS의 NFR-25 (외부 원문 무단 복제 금지) 정합.
- **stub**: `workers/cleanup/naver_news_cleanup.py` 에 위 룰 구현. `COLLECTION_CRON` 과 별도 `NAVER_CLEANUP_CRON=0 17 * * *` (UTC) 환경변수.

### P1-7. SRS docx 와이어프레임 PNG 추출 경로
- **원본**: 본 정리 작업에서 신규 식별. SRS 분할 파일과 `ux/wireframes.md` 의 PNG 링크 부재 안내(이미 본 정리 작업으로 박스 추가됨).
- **default**: **PNG 추출 안 함**. `ux/wireframes.md` 의 화면별 상태 머신 Mermaid + 정상/빈/오류 매트릭스를 단일 권위 소스로 사용. A9는 본 Mermaid·매트릭스만 보고 6개 화면을 구현한다.
- **stub**: 추후 발표용 슬라이드에 와이어프레임 이미지가 필요하면 `unzip SKKU_InSight_SRS.docx -d /tmp/srs-docx && cp /tmp/srs-docx/word/media/* assets/` 로 추출 (본 저장소 외부 한정).

### P1-8. vLLM의 DoRA 어댑터 호환성 + A.X-4.0-Light 로드 검증 — **해결됨 (2026-05-11)**
- **원본**: `algorithms/clickbait-integration.md` §서빙 엔진(vLLM)
- **결정**: stub 옵션 (a) 채택. **DoRA scaling을 사전에 base에 merge → vLLM이 일반 base로 로드** (multi-LoRA serving 미사용). vLLM의 DoRA 직접 지원 여부와 무관하게 동작. base 모델은 [skt/A.X-4.0-Light](https://huggingface.co/skt/A.X-4.0-Light) 공식 모델 카드의 vLLM 서빙 예시로 호환성 가정.
- **구현**: `clickbait_module/scripts/merge_adapter.py` (peft `merge_and_unload()` + `chat_template.jinja` + `run_meta.json` 복사), `clickbait_module/app/inference.py` (vLLM `AsyncLLMEngine` 부트).
- **잔여 작업**: GPU 환경에서 1회 머지 + 부트 + 학습 평가 1건 sanity check (코드 외부 운영 작업).

---

## P2 — 폴리시·검증 단계 (7건, 활성 5 + 해결 2)

### P2-1. OpenAPI 자동 생성 결과 cross-check
- **원본**: `api/auth.md:82`
- **default action**: A2가 FastAPI 부트스트랩 후 `python -m app.openapi_dump` 로 OpenAPI YAML을 출력하고, `docs/api/*.md` 의 시그니처와 자동 비교하는 단순 diff 스크립트를 `scripts/check_api_docs.py` 에 둔다. CI에서 비교.
- **시점**: A2 작업 마지막 단계.

### P2-2. 시드 페르소나 문서 ID 캡처
- **원본**: `data/seed-personas.md:162`
- **default action**: A12가 시드 스크립트 1회 실행 후 생성된 Document ID를 `scripts/fixtures/seed_documents.json` 에 캡처해 시연 재현성 확보.
- **시점**: A12 작업 + A11 (test-ci) 가 동일 fixture 사용.

### P2-3. 빅테크 RSS URL 검증
- **원본**: `data/sources-registry.md` 의 `# TODO: verify URL` 약 22개
- **default action**: A4가 부트 시 `python -m app.source_adapters.verify_urls` 를 실행해 각 RSS의 200 응답 + `<rss>` 또는 `<feed>` root tag 존재를 검증. 실패 시 `enabled=false` 자동 마킹 + 관리자 콘솔 알림. 마커는 검증 통과한 항목부터 제거.
- **시점**: A4 작업 초반.

### P2-4. 빅테크 소스 50–80개로 확장
- **원본**: `data/sources-registry.md:392`, `:482`
- **default action**: 1차 시연은 현재 골격(약 30개)으로 충분. 추가 회사는 사용자 시연 후 우선순위 결정. `sources.yaml` 에 새 엔트리만 추가하고 alembic data migration 1개로 반영하는 패턴이 이미 정립됨.
- **시점**: M4 (발표 준비) 직전 또는 후순위.

### P2-5. 관리자가 사용자 비밀번호를 강제 reset 하는 임시 endpoint
- **원본**: `security/password-policy.md:111`
- **default action**: **시연 동안은 미구현.** 사용자가 잊으면 `docker compose exec api python -m app.scripts.reset_password user@example.com` CLI로 직접 임시 비번 발급. 운영 단계에서 endpoint 검토.
- **시점**: 본 1차 구현 범위 외.

### P2-6. clickbait-detector 외부 인터페이스 가정 — **해결됨 (2026-05-11)**
- **원본**: `algorithms/clickbait-integration.md:107` 영역 (해소되어 마커 정리됨)
- **결정**: P0-1 해결과 함께 자연 해소. 모듈 위치 = `clickbait_module/`. 서빙 엔진 = vLLM. 호스팅·transport는 운영 결정. 자세히는 P0-1 + P1-8 + P2-7.

### P2-7. vLLM 기반 추론에서 next-token "0"/"1" logprob 추출 방식 — **해결됨 (2026-05-11)**
- **원본**: `algorithms/clickbait-integration.md` §서빙 엔진(vLLM)
- **결정**: `SamplingParams(max_tokens=1, logprobs=K, temperature=0.0)` greedy 추론 후 첫 번째 생성 토큰의 logprob 분포에서 id0=56, id1=57 추출 → 2-class softmax. K=20 default(env `LOGPROBS_TOPK`).
- **구현**: `clickbait_module/app/inference.py` (`ClickbaitEngine.classify`). id0/id1은 `adapter/run_meta.json` 권위 사용 (없으면 토크나이저 폴백 + warning).
- **잔여 작업**: 학습 시점 transformers 기반 산식(p0/p1/score_logit_diff)과 vLLM 추론 결과 sanity check 1건 (GPU 환경에서, NFR-09 검증과 함께).

---

## 요약 표

| 우선순위 | 건수 | 차단 에이전트 | 처리 방식 |
|---|---|---|---|
| P0 | 0 (해소됨) | (없음) | 모두 해결 — 모든 에이전트 진행 가능 |
| P1 | 8 (해결 1, 활성 7) | (없음) | reasonable default + stub |
| P2 | 7 (해결 2, 활성 5) | (없음) | 후속 폴리시 단계 |

**모든 P0 해소됨. P1-P2 활성 항목들은 default·stub 경로가 정해져 있어 모든 에이전트(A2-stub 포함)가 즉시 작업 시작 가능.**

## 본 백로그의 출처

본 문서는 다음 위치의 마커를 한 번에 수집·정리한 결과다. 각 마커는 그대로 유지되며, 해결 시점에 해당 라인의 `<!-- TODO: -->` 또는 `# TODO:` 를 제거한다. 본 백로그 자체는 진척에 따라 갱신.

- `algorithms/cold-start.md` (2)
- `algorithms/clickbait-integration.md` (3, P0-1·P2-6 해소되어 마커 제거; vLLM 결정 추가)
- `algorithms/interest-bayesian.md` (1)
- `api/auth.md` (1)
- `data/cso-import.md` (1)
- `data/schema.md` (1)
- `data/seed-personas.md` (1)
- `data/sources-registry.md` (~22 verify URL + 2 보강)
- `security/password-policy.md` (1)
- `sdd/architecture.md` (1, P0-1 해소되어 마커 제거)
- `ux/wireframes.md` (정리 작업으로 본 백로그 P1-7로 이동)

마지막 정리: 2026-05-11 (P0-1·P2-6 해소 + clickbait_module 코드 phase 완료로 P1-8·P2-7 해결 — 합계 4건 해소).
