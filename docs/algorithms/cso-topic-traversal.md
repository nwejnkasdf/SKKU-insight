# 알고리즘: 사용자 × CSO 토픽 traversal trace

본 파일은 SKKU InSight의 사용자 관심 모델을 정의한다. **사용자 관심은 단일 노드가 아니라 사용자가 CSO 그래프 위에서 historical하게 흘러간 path 자체가 하나의 관심 상태 객체**라는 모델이다. 이 path를 `traversal trace`라 부른다. SRS Open Issue 5(사용자 × CSO 상태 머신·전이 룰)를 해결한다.

관련 FR: FR-09, FR-12, FR-13, FR-14, FR-17~20, FR-26, FR-37~41, FR-46~48, FR-55, FR-59. 관련 NFR: NFR-04~06.

연관 문서:
- [`leaf-topic-lifecycle.md`](leaf-topic-lifecycle.md) — dynamic leaf의 라이프사이클 + trace operation 시 leaf 처리
- [`interest-bayesian.md`](interest-bayesian.md) — 베이지안 사후 + active day 기반 감쇠
- [`recommendation-ranking.md`](recommendation-ranking.md) — current/adjacent/proactive ↔ core/adjacent/discovery 매핑
- [`cso-mapping.md`](cso-mapping.md) — 그래프 탐색 함수 (`find_adjacent`, `find_descendants` 등)
- [`../data/schema.md`](../data/schema.md) — UserCSOTraversal 테이블

## 1. 코어 모델

### 1.1 관심 상태의 단위는 path

```
사용자의 관심 상태 ≠ 단일 CSO 노드
사용자의 관심 상태 = 사용자가 CSO 그래프 위에서 따라간 path (= trace)
```

단일 노드만 보면 "이 사용자는 LLM에 관심이 있다"가 끝이지만, path를 보면 "이 사용자는 AI 광역에서 출발해 NLP를 거쳐 LLM까지 깊이 들어왔고, 그 안에서 RAG·Few-shot 같은 동적 토픽을 만들었다"는 풍부한 추론이 가능하다. 추천·요약·LLM 프롬프트 입력에서 모두 trace 단위로 추론한다.

### 1.2 행동이 root, 명시 선택은 prior boost

- 사용자 관심의 **권위는 행동 신호**.
- onboarding 12 클러스터 선택 또는 settings의 관심 분야 수정(FR-55)은 **14 active day 한정 prior boost**로만 작동.
  - cold-start LLM 입력 자료
  - UserInterestState alpha prior 약간 상승 (예: alpha_prior 1.0 → 2.0)
  - 14 active day 후 prior는 기본값으로 복원
- 명시 선택은 trace를 만들지 않는다. 첫 trace는 사용자가 실제로 활동(클릭/저장 등)한 노드부터 시작한다.

### 1.3 사용자별 multiple trace + 무제한 분기

한 사용자는 동시에 여러 활성 trace를 가질 수 있다 (분기 cap 없음). 각 trace는 서로 다른 영역의 관심을 표현. 예:

- T1: `AI → NLP → LLM` (active)
- T2: `AI → CV` (active)
- T3: `Systems → Distributed Systems → Consensus` (stale)

Stale·archived trace도 history로 보존되어 사용자 재활성화 시 reactivation 가능.

## 2. UserCSOTraversal 데이터 모델

### 2.1 테이블 (요약 — 정의는 [`../data/schema.md`](../data/schema.md))

```python
class UserCSOTraversal:
    trace_id: UUID PK
    user_id: UUID FK → User
    path: list[UUID]                      # ordered, root → 말단. cso_topic_id sequence
    status: Literal["active", "stale", "archived"]
    started_active_day: int               # 생성 시점의 user.active_day_counter
    last_activity_active_day: int         # 마지막 path 변동·인터랙션 시점
    score_tail: float                     # path 끝 노드의 베이지안 사후 평균 (캐시)
```

`path`는 순서 있는 cso_topic_id 배열. 첫 원소가 root (사용자가 실제 첫 활동한 노드), 마지막 원소가 현재 말단 (= current 카테고리 단위).

### 2.2 leaf와의 관계

- `DynamicLeafTopic`은 `DynamicLeafTopicCSOTopic`을 통해 cso_topic_id에 매핑된다 (graph anchored).
- `UserCSOTraversal`은 leaf를 **직접 참조하지 않고** path 위 cso_topic_id를 통해 간접 참조한다.
- 한 leaf가 두 trace의 path에 같은 cso_topic_id가 들어 있으면, 그 leaf는 두 trace 모두에서 참조된다 (다중 참조 자연 허용).

## 3. Trace operation

trace 자체의 operation은 **룰 기반**. LLM은 operation에 수반되는 dynamic leaf 재배치에만 호출.

### 3.1 extend (path 자식 append)

**트리거**: 사용자 path 끝 노드의 자식 cso_topic_id에서 인터랙션 누적 + LLM 검증.

```
1. 자식 노드 c에 대해 인터랙션 카운트 ≥ extend_min_interactions (default 5건)
2. LLM 호출 (model_slot="medium"): "이 자식 노드가 사용자 의도를 더 specific하게 표현하는가?"
3. LLM yes → trace.path.append(c)
4. trace.last_activity_active_day = current
5. leaf는 그대로 keep (재배치 없음)
```

**LLM 호출**: 1회 (extend 검증). leaf 처리는 룰 (keep).

### 3.2 retract (말단 노드 제거)

> Trace의 활동 식음은 **3단계 강등**으로 다룬다: `active → stale → retract(path 단축) → archive`. stale 마킹은 즉시 강등(추천 가중치 ↓ + leaf keep), retract는 path 자체 단축(leaf LLM 재배치), archive는 trace 전체 종결. 각 단계 트리거 조건이 분리되어야 운영 행동이 모순되지 않는다.

| 단계 | 트리거 | LLM | 효과 |
|---|---|---|---|
| **stale 마킹** | 말단 노드 점수 ≤ stale_threshold AND idle ≥ `stale_idle_active_days` (default 21) | ❌ | trace.status = stale, 추천 랭킹 가중치 0.3 적용, leaf는 그대로 keep |
| **retract** | stale 상태가 추가 `retract_after_stale_active_days` (default 14) 누적 | ✅ (leaf 한정) | path 말단 1 노드 pop, 해당 노드 매핑 leaf만 LLM이 path 위 다른 노드로 재매핑 또는 archive |
| **archive** | 누적 stale `archive_after_stale_active_days` (default 90) OR path 길이 0 | ❌ | trace.status = archived, 산하 모든 leaf archive |

```
1. (stale 단계) trace.status = "stale", last_activity_active_day 유지
2. (retract 단계) trace.path.pop() — 말단 노드 제거
   - retract된 노드에 매핑된 leaf들에 대해 LLM 호출 (model_slot="high"):
     "이 leaf들 중 새 path 말단(= 부모 노드) 차원에서 의미 있는 것은? 의미 없으면 archive"
   - LLM 응답에 따라 leaf의 cso_topic 매핑을 새 말단(또는 path 위 다른 노드)로 재매핑, 또는 leaf.status = archived
   - retract 후 trace.status는 stale 유지 (다음 active day 차이로 또 한 단계 retract 가능)
   - 사용자가 다시 path 위 노드에 신호를 주면 trace.status = active 복원
3. (archive 단계) trace.status = "archived"
```

**LLM 호출**: retract·split 시 leaf 한정 1회. stale 마킹·archive는 룰만.

### 3.3 split (분기)

**트리거**: trace 의 **현재 path 말단 (tail) 노드** 그래프 자식 2개가 split window 내(default 7 active days) 모두 extend 임계 도달. (Codex R3-NEW-S3 fix) 본문 구현 (`default.py:evaluate_split`) 은 tail-only fork — path 중간 노드의 자식 부상은 retract 후 별도 trace 처리.

```
1. T 단축 + T'=분기점+B (A7 결정 #20, 2026-05-17):
   - 기존 trace T 의 path = path + child_A (분기점에서 child_A 방향으로 확장)
   - 새 trace T' 생성 — path = path + child_B (분기점에서 child_B 방향으로 확장)
2. 분기점(= 양 trace 의 공통 부모, T 와 T' path 의 직전 끝 노드)에 매핑된 leaf 들에 대해:
   - LLM 호출 (model_slot="high"): "각 leaf 가 child_A vs child_B 중 어느 쪽에 의미 있는가?"
   - 응답에 따라 leaf 의 cso_topic 매핑을 target (child_A or child_B) 로 갱신, 또는 archive
3. T, T' 모두 last_activity_active_day = current
4. active_cap=10 초과 시: 가장 idle stale trace 자동 archive 후 split, 또는 split 거부.
```

**LLM 호출**: 1회 (leaf 분배). split 트리거는 룰.

**의도**: T 가 child_A path 를, T' 가 child_B path 를 표현 — 사용자의 두 관심 영역이
각자 trace 로 분리되어 추천 슬롯에서 양쪽 다 cover. 기존 시도 (T 그대로 유지 + child_B
신규) 는 child_A 방향이 산하 leaf 매핑에만 머무는 단점 → A7 round 1 결정으로 변경.

### 3.4 archive

**트리거**: trace.status가 stale인 채 N active day(default 90) 누적, 또는 path 길이 0이 됨.

```
1. trace.status = archived
2. trace.path 위 모든 노드 매핑 leaf들에 대해 leaf.status = archived (기존 leaf-topic-lifecycle 룰 적용)
3. 추천 후보·대시보드에서 제외, history 보존
```

**LLM 호출**: ❌ (룰만).

### 3.5 stale 마킹 (3단계 강등의 1단계)

§3.2 표 참고. trace.status를 active → stale로만 전환하고 path는 유지. 추가 idle이 누적되면 §3.2의 retract로 진행.

### 3.6 merge (A7 신규, 2026-05-17)

**도입 배경**: trace operation 4 (extend/retract/split/archive) → 5 로 확장 (A7 결정 #17). 사용자 활동이 한 영역으로 수렴 시 또는 splits 후 두 trace 의 path 가 충분히 겹쳐 분리 보존 가치 없을 때 두 trace 를 통합.

**트리거 (룰 + LLM 결합, 결정 #21)**:

| 조건 | 의미 |
|---|---|
| 두 active trace path 가 같은 cso_topic_id ≥ `merge_path_overlap_min` (default 3) 공유 | 의미 영역 중첩 |
| 한 path 가 다른 path 의 proper subset | 한쪽이 다른쪽을 완전 포함 |

룰 trigger 만족 후 LLM `trace_merge_verify` 호출 — "두 trace 가 의미상 동일 관심 영역인가?" 판단. LLM merge 결정 시 execute_merge.

**Winner 결정 (결정 #22)**:

```
1. winner = max(last_activity_active_day) — 더 최근 활동 trace
2. tie 시 trace_id 더 작은 쪽 (deterministic, plan TBD)
```

**Execute (결정 #22)**:

```
1. winner trace.path 유지 + last_activity 갱신
2. loser trace.status = "archived" + loser.merged_into_trace_id = winner.trace_id (alembic 0005 신규 컬럼)
3. loser 산하 leaf 들 — winner.path 위 노드에 이미 매핑된 leaf 는 skip,
   미매핑 leaf 는 첫 매핑 cso_topic 을 winner.path 끝 노드로 재매핑
4. winner 의 추천 가중치 증가 효과 (활동도 합산 의미)
```

**LLM 호출**: 1회 (trace_merge_verify). 룰 trigger 는 daily 18 UTC cron (A6 INTEREST_DECAY 와 같은 시각, decision #23). user-mutex (traversal_lock) 공유.

**Audit/recovery**: `UserCSOTraversal.merged_into_trace_id: UUID | None` FK (self) 컬럼 — winner trace 가 archive 또는 삭제되어도 ondelete='SET NULL' 로 loser row 보존.

### 4.1 propagation 룰

leaf에 대한 사용자 인터랙션은 leaf의 부모 cso_topic_id 점수 + path 위 활성 조상 노드 점수로 가산된다.

```
사용자가 leaf "RAG"(부모 = LLM cso_topic_id) 카드를 click(+1.0):

1. leaf 본인 activity 카운터 +1
2. LLM cso_topic_id에 +1.0 (부모 직접)
3. trace에 LLM이 path에 포함되어 있으면, path 위 조상에 1-hop 0.5 감쇠로 propagate:
   - LLM (path 말단): +1.0
   - NLP (path[1]): +0.5
   - AI (path[0] = root): +0.25
4. trace 외 조상 (그래프 상 조상이지만 사용자 어떤 trace 의 path에도 없음)에는 propagate X
```

### 4.2 propagation 가중치 (`interest_params.toml`)

```toml
[propagation]
hop_decay = 0.5                   # 1-hop마다 0.5 곱
max_hops = 4                      # 최대 4-hop까지 propagate (root 끝까지)
non_trace_ancestor_propagate = false
```

### 4.3 의의

- 사용자가 leaf만 활성으로 다뤄도 trace 자체가 stale로 마킹되지 않음 (시나리오 4 자연 해소).
- trace 깊은 노드의 활동이 path 위 모든 노드의 점수를 살림 — path 자체가 활성 상태 유지.

## 5. Active day 기반 시간 단위

### 5.1 active day 정의

**그날 사용자 인터랙션이 1건 이상 있으면 active day**.

- 인정: click, save, hide, not_interested, dwell_tick (단 dwell_tick은 1건이라도 있으면 active 인정)
- 인정 X: 자동 폴링, refresh token 갱신, 단순 dashboard 조회만

### 5.2 active_day_counter 운영

```
User.active_day_counter: int (default 0, 단조증가)

매일 사용자의 첫 인터랙션 발생 시점에:
  if last_active_calendar_date < today:
    user.active_day_counter += 1
    user.last_active_calendar_date = today
    user.save()
```

### 5.3 N active day 임계 적용 위치

| 항목 | wallclock 옛 값 | active day 새 기준 |
|---|---|---|
| onboarding prior 유지 | (미정) | 14 active days |
| trace stale 마킹 | (미정) | path 말단 노드 21 active days 무신호 |
| trace archive | (미정) | stale 90 active days 후 archived |
| trace split window | (미정) | 7 active days 내 두 자식 임계 도달 |
| leaf emerging→active | 7일 + 5건 + 인터랙션 ≥ 2 | 7 active days + 5건 + 인터랙션 ≥ 2 |
| leaf active→stale | 21일 무신호 | 21 active days 무신호 |
| leaf stale→archived | 90일 | 90 active days |
| leaf emerging→archived | 14일 | 14 active days |
| 베이지안 단기 감쇠 | wallclock 7일 반감기 | 7 active days 반감기 |
| 베이지안 장기 감쇠 | wallclock 60일 반감기 | 60 active days 반감기 |

### 5.4 시간 entity 컬럼

```python
# 모든 시간 종속 entity에 다음 컬럼 추가
created_active_day: int       # entity 생성 시점의 user.active_day_counter
last_event_active_day: int    # 마지막 관련 이벤트 시점

# 임계 평가
days_idle = user.active_day_counter - entity.last_event_active_day
if days_idle >= threshold:
    transit_state(entity)
```

## 6. 카테고리 ↔ 추천 슬롯 매핑

### 6.1 카테고리 정의 (사용자별, 매번 derive)

| 카테고리 | 정의 | 추천 슬롯 |
|---|---|---|
| **current** | 모든 active trace의 path 끝 노드 + 그 산하 active dynamic leaf | core (5개 목표) |
| **adjacent** | 모든 active trace 끝 노드의 1-hop 그래프 이웃 (각 trace의 path에 안 들어 있는 노드) | adjacent (3개 목표) |
| **proactive** | 사용자의 어떤 active trace path에도 들어 있지 않은 영역의 trust=high 트렌드 + emerging dynamic leaf 후보 | discovery (2개 목표) |

### 6.2 emerging leaf의 노출 경로 (C-4 해소)

emerging dynamic leaf는 trace의 path 끝 노드 산하에서 분기되므로, 자연스럽게 **current(=core)** 카테고리에 포함된다. 별도 강제 노출 룰 불필요. 단:

- emerging이 너무 많으면 core 슬롯 5개 안에 배치 어려움
- 권장 정책: core 슬롯 5개 중 1개를 "emerging leaf 우선"으로 배치 (`recommendation.toml.core_slot_emerging_quota = 1`).

자세히는 [`recommendation-ranking.md`](recommendation-ranking.md).

## 7. Cold-start과 trace 시작

### 7.1 cold-start 직후

- onboarding 선택 12 클러스터 → cold-start LLM 호출 ([`cold-start.md`](cold-start.md)) → 첫 10개 추천 생성
- 사용자가 첫 추천 카드 클릭 시점에:
  - 클릭한 카드의 Document → DocumentTopic → cso_topic_id 추출
  - 그 cso_topic_id를 root로 하는 새 UserCSOTraversal 생성 (path = [cso_topic_id], status=active)
- 사용자가 다른 카드 클릭하면:
  - 그 카드의 cso_topic_id가 기존 trace path 위에 있으면 → 해당 trace에 인터랙션 누적 (extend 트리거 검토)
  - 없으면 → 새 trace 생성 (또는 path 위 노드와 그래프 거리 1-hop 이내면 split 트리거 검토)

### 7.2 cold-start 후 14 active day 동안

- onboarding 선택 cluster의 prior boost 유지 (alpha_prior 상승)
- 14 active day 후 prior 기본값 복원

## 8. Settings 관심 분야 수정 (FR-55)

사용자가 설정 화면에서 관심 분야를 수정하면:

- **추가**: 새 cluster를 14 active day prior boost 대상으로 추가. 기존 trace는 영향 없음.
- **제거**: 사용자가 명시적으로 cluster 1개를 제거하면 그 cluster를 root로 하는 모든 active trace를 stale 마킹. 단 dynamic leaf는 LLM 검토 후 다른 trace로 인계 가능.

## 9. 의사 코드 (전체 흐름)

> **동시성 가드**: `ingest_user_event`는 한 사용자의 동시 이벤트가 같은 trace에 race를 일으키지 않도록 user-level mutex(Redis lock 또는 PG advisory lock)로 직렬화해야 한다. 자세히는 [`../sdd/concurrency.md §3`](../sdd/concurrency.md). active_day_counter 갱신은 atomic SQL ([`concurrency.md §4.2`](../sdd/concurrency.md)).

```python
async def ingest_user_event(event: UserEvent, user: User):
    # user-level mutex로 직렬화 (concurrency.md §3)
    async with redis_user_lock(user.user_id, key="traversal", ttl=10):
        await _ingest_user_event_locked(event, user)


async def _ingest_user_event_locked(event: UserEvent, user: User):
    # active_day_counter atomic 갱신 (concurrency.md §4.2)
    user.active_day_counter = await maybe_increment_active_day(user.user_id, event.occurred_at.date())

    document_topics = get_document_topics(event.document_id)  # cso + leaf
    for topic_match in document_topics:
        cso_id = topic_match.cso_topic_id

        # 1. 기존 trace에 매칭 시도
        active_traces = await get_active_traces(user.user_id)
        matched_trace = next(
            (t for t in active_traces if cso_id in t.path),
            None,
        )

        if matched_trace:
            # 2. 매칭된 trace에 인터랙션 누적, propagation
            await update_trace_score(matched_trace, cso_id, event.weight)
            await propagate_to_ancestors(matched_trace, cso_id, event.weight, hop_decay=0.5)
            # 3. extend 트리거 검토 (자식 노드에서 임계 충족이면 LLM 검증 후 extend)
            await check_extend(matched_trace, event)
        else:
            # 4. 기존 trace 외부 — 그래프 거리 검토
            close_trace = find_close_active_trace(active_traces, cso_id, max_hops=1)
            if close_trace:
                # split 또는 인접 영역 활동
                await check_split(close_trace, cso_id, event)
            else:
                # 5. 새 trace 생성
                await create_new_trace(user, cso_id, event)

    # 6. retract 평가 (모든 active trace 대상, 일일 배치로도 가능)
    await evaluate_retract_for_user(user)


async def daily_user_decay(user: User):
    """일일 자정 cron, but active_day 기반."""
    if user_was_active_today(user):
        user.active_day_counter += 1
        await apply_bayesian_decay(user, hops=1)  # 단/장기 감쇠 (active day 기반)
        await mark_stale_traces(user)
        await archive_stale_traces(user)
```

## 10. SRS Open Issue 매핑

| Open Issue | 해결 |
|---|---|
| 5. 사용자 × CSO 토픽 상태 머신·전이 룰 (본 인터뷰에서 신규 식별) | 본 문서 §1~6 |
| C-4. emerging leaf 노출 경로 (이전 분석) | §6.2 — current 영역에 자연 포함 + core slot quota |

## 11. 운영 가드

| 항목 | 권장 cap |
|---|---|
| 사용자당 active trace 수 | ≤ 10 (UI 부담·LLM 비용) |
| 사용자당 path 최대 깊이 | ≤ 8 (CSO 그래프 일반 깊이) |
| trace operation LLM 호출 횟수 | 사용자당 일 ≤ 2 (retract + split 평균) |
| propagation max hops | 4 |
| 동시성 직렬화 단위 | 사용자당 1 traversal mutation in-flight (Redis lock — [`../sdd/concurrency.md §3`](../sdd/concurrency.md)) |
| LLM 동시 호출 cap | 전역 8, 사용자당 2 ([`../sdd/concurrency.md §5`](../sdd/concurrency.md)) |

`topic_lifecycle.toml` (확장):

```toml
[traversal]
extend_min_interactions = 5
extend_window_active_days = 7
# 3단계 강등 — active → stale → retract → archive
stale_threshold_score = 0.30                 # 말단 노드 점수 임계
stale_idle_active_days = 21                  # active → stale 마킹
retract_after_stale_active_days = 14         # stale → retract (path 단축)
archive_after_stale_active_days = 90         # stale → archived
split_window_active_days = 7
max_active_traces_per_user = 10
max_path_depth = 8

[propagation]
hop_decay = 0.5
max_hops = 4
non_trace_ancestor_propagate = false
```
