# A12 — Demo Seed (Phase 4)

> 작업 디렉토리: ``
> **사전조건**: A2~A10 완료 (모든 모듈 동작). A11과 병렬 가능. **본 모듈이 시연 안정성의 핵심**.

## 너의 역할

5+ 페르소나 자동 생성 + 14 active day 인터랙션 시뮬레이션 + LLM mock fixture 캡처 + 시연 시나리오 자동 부트. 시연 직전에 깨끗한 환경에서 5분 안에 모든 데이터가 만들어지도록.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `docs/data/seed-personas.md` (전체 — 페르소나 6명, daily pattern)
- `docs/decisions.md` §9 (데모 시나리오 5개)
- `docs/sdd/deployment.md` (시연 부트 절차)
- `docs/algorithms/cold-start.md` (mock fixture 패턴)
- `docs/sdd/concurrency.md` (active_day 시뮬레이션)

## 산출

### 1. `scripts/seed_personas.py` (seed-personas.md 그대로)
- 6 페르소나 생성: persona_01 (LLM 학부생), persona_02 (시스템 학부생), persona_03 (VLM 연구자), persona_04 (분산시스템 교수), persona_05 (일반), admin_01 (관리자)
- 각 페르소나 signup → consent → onboarding (cluster 선택) → cold-start
- `--full` 옵션: 14 active day 인터랙션 replay
- `--no-events`: 사용자만, 인터랙션 생략
- `--advance-active-days N`: 시연 시뮬레이션 (active_day_counter += N)

### 2. 14 active day 인터랙션 replay
- `seed-personas.md` "DAILY_PATTERN" 그대로
- 각 페르소나 daily pattern (click·save·hide·dwell·noise·not_interested) 비율로 이벤트 생성
- 매 day 끝마다 `daily_decay` 트리거 (active day 단위)
- 시간 jitter: 09:00~22:00 KST 분포
- 동시 부하 X (순차로 INSERT 후 베이지안 일괄 update)

### 3. LLM mock fixture 캡처
- `tests/fixtures/mock_llm/` 디렉토리
- cold-start fixture: 페르소나별 첫 10 카드 결과 미리 캡처 (5 페르소나 × 5 cluster 조합 ≈ 25 fixture)
- leaf identify fixture: 매 day 시뮬레이션 후 결과 캡처 (페르소나 × 14일 ≈ 70 fixture)
- prompt hash → JSON 매핑 (`MockProvider` 가 직접 read)

### 4. 시연 부트 자동화
- `make demo-bootstrap` Makefile target (deployment.md §시연 부트)
  ```
  docker compose down -v
  docker compose up -d postgres redis
  make migrate
  make import-cso          # A3
  make create-admin        # A2
  make seed --full         # 본 세션
  docker compose up -d
  make smoke-test          # 5+ 페르소나 dashboard 호출 검증
  ```

### 5. smoke-test
- `scripts/smoke_test.py` — 시연 직전 5분 안에 모든 흐름 동작 확인
- 페르소나별 login → dashboard → 카드 클릭 → 베이지안 변화 → trace 변화 → admin 콘솔 접근 차단 (AT-13)

### 6. 시연 시나리오 5 매핑 (decisions.md §9)
- 각 시연 시나리오를 자동 reproduction 가능하도록 fixture 준비
- 시나리오 1 (신규 가입 cold-start) — admin 외 새 user 1명
- 시나리오 2 (카드 클릭/저장/숨김 → 베이지안) — persona_01
- 시나리오 3 (다음 active day → emerging→active) — persona_03 (긴 dwell)
- 시나리오 4 (수집 실패 재실행) — admin_01 + 의도적 실패 1건 시드
- 시나리오 5 (동의 철회 → 분기) — persona_05

## 헌법 (재강조)

- **시연 안정성 = mock fixture 품질**. LLM 환상 응답이 시연에 노출되지 않도록 fixture 사전 검수.
- **active_day 시뮬레이션 정확성**: 실제 14일 wallclock 대신 active_day_counter 직접 +N (concurrency.md §4.2 패턴 활용).
- **fixture는 deterministic**: 동일 prompt → 동일 응답. 시연 재현성.
- **smoke-test는 시연 5분 전 필수**.

## 검증

```bash
make demo-bootstrap
# 5분 이내 완료, 페르소나 6명 + 14 active day 인터랙션 + 시연 fixture 준비

make smoke-test
# 모든 페르소나 dashboard 200 응답
# AT-13 권한 분리 통과
# trace·leaf·베이지안 일관성 확인

# 시연 시나리오 1~5 reproduction
python scripts/run_demo_scenario.py --scenario 1 --persona new_user
python scripts/run_demo_scenario.py --scenario 3 --persona persona_03 --advance-active-days 1
# 등

mypy --strict scripts/
ruff check scripts/
pytest backend/tests/seed -v
```

## 출력 형식

기본 + 추가:
- 6 페르소나 생성 + 14일 인터랙션 replay 통과
- LLM mock fixture 갯수 (cold-start + leaf identify)
- demo-bootstrap 소요 시간
- smoke-test pass/fail
- 시연 시나리오 5개 reproduction 검증
- A11 test-ci와 fixture 협업 사항
