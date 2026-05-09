# A7 — Leaf Lifecycle + Traversal Engine (Phase 2)

> 작업 디렉토리: ``
> **사전조건**: A2 + A3 + A4 + A6 완료. 본 모듈이 가장 알고리즘 깊은 부분.

## 너의 역할

**본 프로젝트의 head**. 사용자 관심 모델의 핵심:
- `UserCSOTraversal` trace operation (extend / retract / split / archive)
- 3단계 강등 (active → stale → retract → archived)
- `DynamicLeafTopic` 라이프사이클 (emerging → active → stale → archived, merged)
- LLM은 신규 leaf 식별 + 병합 + retract/split 시 leaf 재배치에만

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `docs/algorithms/cso-topic-traversal.md` (전체, 핵심 SOR)
- `docs/algorithms/leaf-topic-lifecycle.md` (전체)
- `docs/data/schema.md` (UserCSOTraversal, DynamicLeafTopic, DynamicLeafTopicCSOTopic)
- `docs/sdd/module-boundaries.md` (`TraversalEngine`, `LifecycleEvaluator` 추상)
- `docs/sdd/concurrency.md` §3 user-mutex
- `docs/algorithms/interest-bayesian.md` §propagation (A6와 협업)

## 산출

### 1. `app/traversal/` 모듈
- `protocol.py` — `TraversalEngine` Protocol (module-boundaries.md 그대로)
- `default.py` — `DefaultTraversalEngine` 구현체
- `operations.py` — extend / retract / split / archive 4 함수 (cso-topic-traversal.md §3)
- `propagation.py` — A6의 `propagation.py` 와 통합 또는 호출

### 2. `app/leaf_lifecycle/` 모듈
- `protocol.py` — `LifecycleEvaluator` Protocol
- `hybrid_d.py` — `HybridDLifecycleEvaluator` (D 하이브리드)
- `rule_evaluator.py` — 룰 기반 emerging→active, active→stale (leaf-topic-lifecycle.md 의사 코드)
- `llm_identifier.py` — 매 일일 수집 직후 신규 leaf 식별 LLM 호출
- `merge_evaluator.py` — 주 1회 병합 평가 LLM

### 3. user-level Redis lock
- 모든 trace mutation은 `redis_user_lock(user_id, key="traversal", ttl=10)` 안에서 실행 (concurrency.md §3)
- `RedisKey.traversal_lock(user_id)` 사용

### 4. 3단계 강등
- `cso-topic-traversal.md §3.2` 표 그대로:
  - stale 마킹 (21 active days 무신호) — 즉시 status 전환, leaf keep
  - retract (추가 14 active days, path 단축 + leaf LLM 재배치)
  - archived (90 active days 누적)
- 각 단계 트리거 평가는 사용자 인터랙션 직후 또는 일일 배치

### 5. LLM 호출 패턴
- **신규 emerging 식별**: 매 일일 수집 직후, model_slot="high" (cso-topic-traversal.md §3.2 leaf-topic-lifecycle.md "Identify Emerging" 프롬프트)
- **병합 평가**: 주 1회, model_slot="high" ("Evaluate Merges" 프롬프트)
- **retract leaf 재배치**: trace retract 시 1회 호출, model_slot="high" ("Retract 시 LLM 프롬프트" leaf-topic-lifecycle.md)
- **split leaf 분배**: trace split 시 1회 호출, model_slot="high"

### 6. trace 시작 (cold-start과 협업)
- 사용자 첫 카드 클릭 시점에 `UserCSOTraversal` 1건 생성 (cso-topic-traversal.md §7.1)
- A6 ingest_event에서 매칭 trace 없으면 본 세션의 `traversal.create_new_trace()` 호출

### 7. 룰 기반 전이 + LLM 호출 분리
- extend / retract / split / stale 마킹 / archive: 룰만 (cso-topic-traversal.md §3 표)
- LLM은 leaf 관리에만 (신규 식별, 병합, retract leaf 재배치, split leaf 분배)

### 8. config 파일
- `topic_lifecycle.toml` — cso-topic-traversal.md §11 + leaf-topic-lifecycle.md "topic_lifecycle.toml" 통합

### 9. propagation A6 협업
- A6의 propagation 모듈에서 본 모듈의 `get_active_traces(user_id)` 호출
- 1-hop 0.5 감쇠 (cso-topic-traversal.md §4)

## 헌법 (재강조)

- **trace operation은 룰**. LLM은 leaf 한정.
- **path 위 노드는 graph anchored** (cso_topic_id). leaf의 cso_topic_id 매핑이 trace path에 포함되어 있을 때만 그 trace에서 참조.
- **active day 기준** (cso-topic-traversal.md §5). wallclock 아님.
- **`trace_anchor_required = true`** (leaf-topic-lifecycle.md): 신규 emerging은 사용자 active trace path 위 노드 산하에서만 분기.
- **사용자당 active trace cap 10, path 깊이 cap 8** (cso-topic-traversal.md §11).

## 검증

```bash
docker compose up -d
# 시드 페르소나 1명 + 14일치 인터랙션 (A12 의존)
# 또는 직접 fixture 시뮬레이션:
docker compose exec api python -c "
from app.traversal.engine import DefaultTraversalEngine
# extend / retract / split 시뮬레이션
"

# trace operation race 검증 (concurrency)
ab -n 50 -c 10 -p event.json -T application/json http://localhost:8000/events
# UserCSOTraversal.path 가 더블 append 안 되는지

mypy --strict backend/app/traversal backend/app/leaf_lifecycle
ruff check
pytest backend/tests/traversal backend/tests/leaf_lifecycle -v

# Mock LLM provider 사용 (deterministic fixture)
LLM_PROVIDER=mock pytest backend/tests/leaf_lifecycle/test_identify_emerging.py
```

테스트:
- extend (자식 임계 + LLM 검증) → path append
- retract (말단 stale 14일 + LLM leaf 재배치) → path pop + leaf 매핑 재배치
- split (두 자식 동시 부상) → 새 trace + leaf LLM 분배
- archive (stale 90일) → trace.status=archived + leaf 동반 archive
- 3단계 강등 시퀀스 (active→stale→retract→archived)
- LLM JSON parse 실패 fallback (TopicLinkageError)
- user mutex race 방어

## 출력 형식

기본 + 추가:
- TraversalEngine·LifecycleEvaluator 두 추상 + 구현체 검증
- LLM 호출 횟수 시뮬레이션 (사용자 1명·14일 → 평균 N회)
- trace operation·leaf transition fixture 시뮬레이션 결과
- A6 propagation과의 통합 검증
- A8 recommendation이 봐야 할 사항 (current/adjacent 데이터 형태)
