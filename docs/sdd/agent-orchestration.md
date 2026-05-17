# 멀티 에이전트 오케스트레이션

본 파일은 SKKU InSight를 여러 Claude/Codex 에이전트 세션으로 구현할 때, **통신 규격 깨짐**과 **시그니처 표류**를 자동 차단하기 위한 운영 룰을 정리한다. 사용자는 알고리즘·결정 검수에 시간을 집중하고, 인터페이스 일관성은 도구가 보장한다.

연관 문서: [`contracts.md`](contracts.md) (단일 SOR enum·error code·Redis key), [`api-conventions.md`](api-conventions.md) (HTTP 표준), [`concurrency.md`](concurrency.md), [`../ops/ci-cd.md`](../ops/ci-cd.md).

## 1. 5겹 방어

| Layer | 도구 | 차단 대상 |
|---|---|---|
| 1. Contract-first | `backend/app/contracts.py` (사용자 1회 작성, 모든 에이전트 import) | enum 표류, error code 미세 차이, Redis key 컨벤션 |
| 2. OpenAPI codegen | `scripts/export_openapi.py` → client/admin TypeScript codegen | API 시그니처 표류 |
| 3. Cross-check 스크립트 | `scripts/check_*.py` 6종 | docs ↔ 코드 drift |
| 4. Strict type | mypy strict + ruff + tsc strict | 함수 시그니처 mismatch |
| 5. Phase별 순차 호출 | 본 문서 §3 | 병렬 race로 인한 인터페이스 충돌 |

## 2. 에이전트 헌법

각 에이전트 세션은 다음 룰을 **반드시** 따른다. 위반 시 PR reject.

1. **다른 모듈의 시그니처·enum·error code를 새로 정의하지 마라**. `contracts.py`와 자기 모듈에 이미 있는 것만 사용. 필요하면 사용자에게 PR로 요청 + 차단.
2. **OpenAPI YAML이 SOR**. client·admin은 codegen 결과만 import. endpoint를 raw fetch로 호출 금지.
3. **DB 스키마 변경은 alembic migration + `docs/data/schema.md` 동시 수정**. 한쪽만 수정 금지.
4. **새 환경변수 추가는 `BaseSettings` + `docs/ops/env-vars.md` + `.env.example` 셋 동시 수정**. 한쪽만 금지.
5. **자기 모듈 외 파일 수정 시 명시**: PR description에 "이 PR은 X 모듈도 수정함" 명시. 사용자가 통합 영향 검수.
6. **통신 규격 변경 (시그니처·enum·키)은 자기 모듈 안에서 수정 금지**. 별도 contracts PR로 사용자 결정 받음.
7. **TODO 마커는 `<!-- TODO -->` + `decision-backlog.md` 항목 추가** 둘 다.
8. **테스트 작성 필수**: 자기 모듈에 unit + integration test. CI가 강제.

## 3. Phase별 순차 호출

```
Phase 0a (1 세션): contracts.py + 모든 endpoint stub ✅ (A1 docs + A2-stub, PR #4)
   ↓ 사용자 검수 30분, OpenAPI export 확인
Phase 0b (2 세션 병렬): A3 CSO 임포트 ✅ (PR #?), A2 인증·동의·온보딩 본문 ✅ (PR #7)
   ↓ 사용자 검수 30분
Phase 1 (3 세션 병렬): A4 collection ✅ (PR #16), A5 clickbait ✅ (외부 PR #2), A6 interest-bayesian ✅ (PR #18 + #19)
   ↓ 사용자 검수 + OpenAPI 갱신 확인 60분
Phase 2 (2 세션 직렬): A7 leaf-lifecycle + traversal ⬜ → A8 recommendation ⬜
   ↓ 사용자 검수 + 시드 페르소나로 end-to-end 90분
Phase 3 (2 세션 병렬): A9 electron-client ⬜ (codegen api.ts import), A10 admin-console ⬜
   ↓ 사용자 검수 + 시연 리허설 60분
Phase 4 (2 세션 병렬): A11 test-ci ⬜, A12 demo-seed ⬜
   ↓ 최종 검수 + 발표 자료
```

**현재 위치**: Phase 1 완료 (2026-05-17). 다음 진입 Phase 2 — **A7 leaf-lifecycle + traversal** (D 하이브리드 + `extend/retract/split/archive` + 3단계 강등 + `INTEREST_PROPAGATION_ENABLED=true` 토글).

총 ~12 에이전트 세션 + 사용자 검수 시간 ~5시간.

### Phase 0a — Stub-only 세션

가장 중요한 세션. 본 세션 산출:

- `backend/app/contracts.py` (enum, error code, Redis key, Pydantic base)
- `backend/app/main.py` (FastAPI 앱 + 모든 router import)
- `backend/app/{auth,consent,onboarding,topic,interest,collection,recommendation,admin}/router.py` — 모든 endpoint signature + `raise NotImplementedError`
- `backend/app/{auth,...}/schemas.py` — Pydantic Request/Response 모델 (구현 X)
- `backend/scripts/export_openapi.py` — FastAPI app → openapi.json 출력

사용자 검수 후 OpenAPI export → `client/src/generated/api.ts` codegen → A9·A10이 이걸 import할 준비 완료.

### 검수 체크포인트별 사용자 작업

| Phase | 사용자 시간 | 검수 항목 |
|---|---|---|
| 0a | 30분 | contracts.py·OpenAPI 표면 vs docs |
| 0b | 30분 | DB 스키마 vs docs/data/schema.md, 인증 흐름 |
| 1 | 60분 | **(v13 라운드)** LLM tool-use 1일치 검색 응답 검증 (MockProvider fixture + 정식 provider 토글), DoRA 통합 (사용자 News 활성화 케이스만), 베이지안 단순 시뮬레이션 |
| 2 | 90분 | trace 시드 페르소나 시뮬레이션, cold-start fixture 검증 |
| 3 | 60분 | Electron 6 화면 동작, 시연 리허설 1회 |
| 4 | 60분 | AT 자동화 결과, 발표 자료 |

총 검수 시간 ~5.5시간 — 일주일 분산 가능.

## 4. 에이전트 간 의존 그래프 (재정리)

```
contracts.py (Phase 0a) ✅ ← 모든 후속 모듈이 import

A2 backend-foundation ✅
  ├─ /auth, /consent, /onboarding, /events, /interest, /topics, /recommendations, /admin
  └─ Pydantic schemas: contracts.py 의 Base 모델 상속

A3 cso-topic ✅ — topic_engine, NetworkX cache
A4 collection ✅ — **(v13 라운드)** LLMProvider.search_with_tools + Document/DocumentTopic/CollectionJob + dedup + jitter
A5 clickbait ✅ (외부 서비스) — DoRA wrap. **(v13 라운드)** 1차 default 비활성, 사용자 News 활성화 시만 호출
A6 interest-bayesian ✅ — atomic UPSERT, propagation (env flag default false), active day daily decay, 14-day boost 만료

A7 leaf-lifecycle + traversal ⬜ — A6 (state) + A3 (graph) 의존. `INTEREST_PROPAGATION_ENABLED=true` 토글 작업 포함
A8 recommendation ⬜ — A7 (current/adjacent) + A6 (bucket) + A4 (Document) 의존

A9 electron-client ⬜ — client/src/generated/api.ts (A2 OpenAPI codegen)
A10 admin-console ⬜ — admin-console/src/generated/api.ts (A2 OpenAPI codegen). `system_config` UI 갱신 책임 (A6 read-only loader 와 분담)

A11 test-ci ⬜ — 모든 모듈
A12 demo-seed ⬜ — 모든 모듈
```

## 5. 자동 검증 강제

CI에서 PR마다 실행:

```yaml
# .github/workflows/contracts.yml
- run: python -m scripts.check_api_docs
- run: python -m scripts.check_schema
- run: python -m scripts.check_env
- run: python -m scripts.check_error_codes
- run: python -m scripts.check_redis_keys
- run: python -m scripts.check_contracts
- run: cd backend && python -m scripts.export_openapi > openapi.json && cd .. && git diff --exit-code openapi.json
- run: cd client && npm run codegen && git diff --exit-code src/generated
- run: cd backend && mypy --strict app/
- run: cd backend && ruff check
- run: cd client && npm run typecheck
```

(repo root 의 `scripts/` 를 패키지로 import 하려면 `python -m scripts.X` 표기 필수 — `python scripts/X.py` 실행은 `from scripts._common import ...` 가 `__main__` context 에서 실패함.)

`git diff --exit-code` 로 codegen 결과가 commit과 일치하는지 강제. 시그니처 변경 후 codegen 안 하면 CI 실패.

### Ownership 표 (어느 에이전트가 무엇을 만드는가)

**`scripts/check_*.py` 6종 (A2 결정 2026-05-11):**

| 스크립트 | 비교 대상 | 소유 에이전트 |
|---|---|---|
| `check_api_docs.py` | OpenAPI export ↔ `docs/api/*.md` endpoint 표 | **A2** |
| `check_schema.py` | SQLAlchemy `Base.metadata` ↔ `docs/data/schema.md` 모델 코드 블록 | **A2** |
| `check_env.py` | `Settings.__fields__` ↔ `docs/ops/env-vars.md` 표 ↔ `.env.example` 3-way | **A2** |
| `check_error_codes.py` | `app.contracts.ErrorCode` ↔ `docs/api/*.md` 오류 표 | **A2** |
| `check_redis_keys.py` | `app.contracts.RedisKey` 메서드 prefix ↔ `docs/sdd/concurrency.md` + AST 스캔으로 raw f-string Redis 키 검출 | **A2** |
| `check_contracts.py` | enum 값 ↔ Alembic CHECK SQL + RedisKey SOR 외부 raw 검출 | **A2** |

사유: CI 게이트의 일관성. 한 에이전트가 6종 모두 작성·유지하면 docs↔코드 정합 검증 패턴이 통일. 이후 모든 PR이 CI에서 동일 게이트 통과.

**RQ + rq-scheduler cron job 등록 (A2 결정 2026-05-11):**

| job | cron env | A2 stub 등록 | 본문 구현 책임 |
|---|---|---|---|
| `account_deletion_job` | (event-driven, enqueue) | A2 본문 구현 | A2 |
| `cold_start_job` | (event-driven, enqueue) | A2 stub | **A8** |
| ~~`naver_cleanup_job`~~ | ~~`NAVER_CLEANUP_CRON`~~ | A2 stub + scheduler 등록 | **(v13 라운드 폐기, 2026-05-11)** decision-backlog P1-6 무효 마킹. NaverBS4 어댑터 폐기로 cleanup 대상 0건. A4 는 등록 제거 또는 비활성. |
| `collection_job` | `COLLECTION_CRON` | A2 stub + scheduler 등록 | **A4** |
| `interest_decay_job` | `INTEREST_DECAY_CRON` | A2 stub + scheduler 등록 | **A6** |
| `merge_evaluation_job` | `MERGE_EVALUATION_CRON` | A2 stub + scheduler 등록 | **A7** |

사유: 본 결정의 5겹 방어 §4(strict type)와 정합. 단일 진입점 `backend/app/scheduler.py`이 모든 cron 등록을 통합 관리. 후속 에이전트는 자기 job의 함수 본문만 채움 → cron 등록·env 소비 보일러플레이트 중복 0.

## 6. 에이전트 작업 표준 프롬프트

각 에이전트 호출 시 사용자가 줄 컨텍스트:

```
[프로젝트 맥락]
- AGENTS.md 읽음
- contracts.py가 SOR — import만 사용, 정의 금지
- docs/api/{your-area}.md 시그니처를 정확히 따름
- 다른 모듈 파일 수정 시 명시

[작업 범위]
{모듈명}: {docs/api/*.md, docs/algorithms/*.md, docs/data/schema.md 해당 부분}

[금지 사항]
- contracts.py 외 enum 정의
- 다른 모듈 import path 변경
- docs와 다른 시그니처
- TODO 마커 추가 시 decision-backlog.md 갱신 빠뜨리지 말 것

[기대 산출]
- backend/app/{module}/ 본 PR
- 단위 테스트
- mypy strict + ruff 통과
- OpenAPI export가 docs와 일치
```

이 프롬프트를 사용자가 매 세션에 복붙. 에이전트가 헌법을 따르도록 강제.

## 7. 위반 발생 시 대응

| 위반 | 대응 |
|---|---|
| CI check_*.py 실패 | PR 자동 close, 에이전트 재호출 with diff 결과 |
| OpenAPI codegen 깨짐 | A2 PR로 contracts·OpenAPI 동시 갱신 |
| 다른 모듈 시그니처 임의 변경 | revert + 사용자가 contracts PR 별도 진행 |
| Race condition 발견 (부하 테스트) | A11이 test 추가 + 해당 모듈 에이전트 재호출 |
| LLM mock fixture 시연 깨짐 | A12가 fixture 재생성 + 사용자 검수 |

## 8. 사용자 시간 분배 권장

총 ~5.5시간 + 디버깅 ~5시간 = 10시간 (1주 분산 가능).

| 작업 | 시간 | 사용자 강점 활용 |
|---|---|---|
| 알고리즘 검수 (traversal·베이지안·라이프사이클) | 3시간 | ✅ 본인 아이디어 |
| 통합 디버깅 (race·LLM 환상) | 2시간 | ⚠️ 도구가 1차 차단, 잔여만 |
| 시연 리허설 + 발표 자료 | 3시간 | ✅ |
| 외부 사이트·DoRA 통합 결정 | 1시간 | ⚠️ 사용자 결정만 필요 |
| 통신 규격 검수 (5겹 방어로 0에 수렴) | ~30분 | ✅ 도구가 차단 |

→ 사용자 시간의 70%가 본인 강점인 알고리즘·시연에 집중. 통신 규격은 자동 도구가 흡수.

## 9. 시연 30분 전 최종 체크

```bash
# 깨끗한 부트
docker compose down -v
docker compose up -d postgres redis
make migrate
make import-cso
make create-admin
make seed --full

# CI 마지막 통과 확인
git status   # clean working tree
git log -1   # 최신 commit이 main에 머지됨

# 통합 smoke test
docker compose up -d
sleep 10
make smoke-test   # 5+ 페르소나 dashboard 호출 + 응답 검증

# Electron 부트
cd client && npm start
```

5분 안에 깨끗한 시연 환경 부팅. 이게 안 되면 시연 직전에 발견되는 race·DB·envvar 누락 문제.
