# A11 — Test + CI/CD (Phase 4)

> 작업 디렉토리: ``
> **사전조건**: A2~A10 완료 (모든 모듈 코드 존재). A12와 병렬 가능.

## 너의 역할

자동화 검증 인프라 구축. pytest + vitest + GitHub Actions + 6 cross-check 스크립트 + AT-01~15 자동화. **사용자가 멀티 에이전트 PR을 검수하지 않아도 인터페이스 표류·정합성·동시성 race를 자동 차단**하는 게 목표.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `docs/ops/ci-cd.md` (전체)
- `docs/sdd/agent-orchestration.md` §5 (CI 검증), §6 (테스트 시나리오)
- `docs/sdd/api-conventions.md` §14 (cross-check + codegen), §16 (테스트)
- `docs/sdd/concurrency.md` §12 (부하 테스트)
- `docs/srs/08-acceptance-tests.md` (AT-01~15)
- `docs/sdd/contracts.md` §9 (CI 검증)

## 산출

### 1. cross-check 스크립트 6종 (`scripts/`)
- `check_api_docs.py` — OpenAPI YAML ↔ docs/api/*.md endpoint 표 일치
- `check_schema.py` — SQLAlchemy 모델 ↔ docs/data/schema.md 컬럼 일치
- `check_env.py` — BaseSettings ↔ docs/ops/env-vars.md 변수 일치
- `check_error_codes.py` — contracts.py::ErrorCode ↔ docs/api/*.md 오류 표 일치
- `check_redis_keys.py` — contracts.py::RedisKey ↔ docs/sdd/concurrency.md 키 일치
- `check_contracts.py` — contracts.py enum ↔ alembic CHECK + raw f-string 금지 검증

### 2. GitHub Actions 워크플로
- `.github/workflows/contracts.yml` — 6 check + OpenAPI/codegen diff (ci-cd.md)
- `.github/workflows/ci.yml` — lint(ruff) + type(mypy --strict, tsc --strict) + test(pytest, vitest)
- `.github/workflows/build.yml` — Docker 이미지 빌드 (api, worker, clickbait, admin-console)
- `.github/workflows/electron-build.yml` — Windows installer (수동 dispatch, 1차 미사용)

### 3. AT-01~15 자동 테스트
- `backend/tests/acceptance/` 디렉토리
- `srs/08-acceptance-tests.md` 표 그대로 1:1 매핑
- 자동화 가능: AT-01~04, AT-07~09, AT-11~13, AT-15 (약 11개)
- 수동 체크리스트: AT-05·06·10·14 (약 4개) — `tests/manual_checklist.md` 작성

### 4. 부하 테스트 (concurrency.md §12)
- `backend/tests/load/test_20_users.py` — 20명 동시 dashboard p95 3초
- `backend/tests/load/test_concurrent_events.py` — 1명 1초 10건 → race 없음
- locust 또는 pytest-asyncio + asyncio.gather

### 5. Mock LLM provider 검증
- 시연 안정성을 위해 `LLM_PROVIDER=mock` 강제 시 모든 fixture 응답 일관 검증
- `tests/fixtures/mock_llm/` 디렉토리 + prompt hash → JSON 매핑

### 6. 단위 테스트 매트릭스
- 모든 모듈 unit test 커버리지 ≥ 80% 목표 (1차 시연은 핵심만 OK)
- API contract test (각 endpoint 200/4xx 응답)
- Concurrency invariant test (race condition 시뮬레이션)
- Algorithm test (베이지안·trace operation·라이프사이클 결정 검증)

### 7. CI 매트릭스
- Python 3.12, Node 20
- PostgreSQL 16 services + Redis 7
- LLM_PROVIDER=mock 강제 (외부 호출 차단)
- contracts.yml 먼저 통과 → ci.yml 실행

## 헌법 (재강조)

- **CI 외부 호출 차단**: `LLM_PROVIDER=mock` 강제, 외부 source는 vcrpy 또는 fixture
- **codegen drift 차단**: `git diff --exit-code openapi.json` + `client/src/generated`
- **자동화 가능한 AT는 모두 자동화** (수동은 11개 중 4개만)
- **race condition test 필수** (concurrency.md §12 시나리오 2개)

## 검증

```bash
# 로컬 CI 시뮬레이션
make ci-local         # 모든 check_*.py + ruff + mypy + pytest + vitest

# AT 자동화 통과 확인
pytest backend/tests/acceptance -v
# AT-01 ✓ AT-02 ✓ AT-03 ✓ AT-04 ✓ AT-07 ✓ AT-08 ✓ AT-09 ✓ AT-11 ✓ AT-12 ✓ AT-13 ✓ AT-15 ✓

# 부하 테스트
pytest backend/tests/load -v
# 20명 dashboard p95 < 3000ms
# 1명 10건 race 검증 (sum = 10×weight)

# GitHub Actions 시뮬레이션 (act 또는 push to draft branch)
gh workflow run contracts.yml
gh workflow run ci.yml
```

## 출력 형식

기본 + 추가:
- 6 check 스크립트 통과 검증 (각자 무엇을 잡았는지)
- AT 자동화 11/15 통과 검증
- 부하 테스트 p95 결과
- CI 워크플로 4종 모두 green
- `tests/manual_checklist.md` 작성 (AT-05·06·10·14 체크리스트)
- 다음 Phase A12가 봐야 할 사항 (mock fixture 협업)
