# 알고리즘: 추천 후보 생성과 랭킹

본 파일은 SKKU InSight 추천 대시보드의 후보 생성, 슬롯 분배, 랭킹 알고리즘을 정의한다. 관련 FR: FR-26, FR-35~45. NFR-05·06 (관심 적합도 1순위), NFR-12 (p95 3초). Cold-start은 [`cold-start.md`](cold-start.md), 모듈 인터페이스는 [`../sdd/module-boundaries.md`](../sdd/module-boundaries.md).

> **사용자 관심 모델 ↔ 추천 슬롯 1:1 매핑**: 본 시스템의 사용자 관심 모델은 [`cso-topic-traversal.md`](cso-topic-traversal.md)의 trace 기반이며, 그 trace에서 derive되는 **current/adjacent/proactive** 3 카테고리가 추천 슬롯 **core/adjacent/discovery**에 1:1 대응한다.
>
> | 카테고리 (관심 모델) | 정의 | 슬롯 (추천) |
> |---|---|---|
> | **current** | 모든 active trace의 path 끝 노드 + 그 산하 active dynamic leaf | **core** (5개) |
> | **adjacent** | 모든 active trace 끝 노드의 1-hop 그래프 이웃 (path 외) | **adjacent** (3개) |
> | **proactive** | 어떤 active trace path에도 없는 영역의 trust=high 트렌드 + emerging dynamic leaf 후보 | **discovery** (2개) |

## 개요

대시보드는 정확히 10개 카드. 후보가 충분하면 `core:adjacent:discovery = 5:3:2`. 슬롯 후보 부족 시 fallback 룰을 적용해 항상 10개를 채운다.

## 후보 생성 — 슬롯별 정의

### Core (FR-39, 5개 목표)

활성 trace path 끝 노드 + 그 산하 active dynamic leaf 기반 (= **current** 카테고리).

```
current_csos = {trace.path[-1] for trace in user.active_traces}
current_leaves = {leaf for leaf in user.active_leaves
                  if leaf.cso_topic_id in {n for trace in user.active_traces for n in trace.path}}

candidates_core = SELECT documents WHERE
    (cso_topic_id IN current_csos OR leaf_topic_id IN current_leaves)
    AND document_id NOT IN (SavedDocument ∪ HiddenDocument)
    AND document_id NOT IN clickbait_documents
    AND topic NOT IN NotInterestedTopic
ORDER BY freshness DESC LIMIT 50
```

> **C-4 해소 (emerging leaf 노출)**: emerging dynamic leaf는 현재 활성 trace path 끝 노드 산하에서만 분기되므로 (cso-topic-traversal.md §1.3), 자연히 current 카테고리 후보 풀에 포함된다. 단 emerging만으로 core 5개를 채우지 않도록 다음 quota 적용:
>
> - core 슬롯 5개 중 **최소 1개는 emerging leaf 우선** (`recommendation.toml.core_slot_emerging_quota = 1`)
> - 나머지 4개는 active leaf와 일반 cso_topic 후보 풀에서 점수 순
> - emerging leaf 후보 부재 시 quota는 active leaf로 자동 회수

### Adjacent (FR-40, 3개 목표)

활성 trace 끝 노드의 1-hop 그래프 이웃 중 어떤 trace path에도 들어 있지 않은 노드 기반 (= **adjacent** 카테고리).

```
trace_path_csos = {n for trace in user.active_traces for n in trace.path}
trace_tail_csos = {trace.path[-1] for trace in user.active_traces}
adjacent_csos = topic-engine.find_adjacent(trace_tail_csos, hops=1) - trace_path_csos - NotInterestedTopic
candidates_adjacent = SELECT documents WHERE topic IN adjacent_csos ...
```

### Discovery (FR-41, 2개 목표)

**(A8-v2 라운드 본문 pivot, 2026-05-19, [`../decisions.md §15`](../decisions.md))** — discovery slot 2 의 본질을 "trust=high trend 정렬" 에서 "사용자 흥미 *궤적의 교차점*에서 새 방향성 발굴" 로 변경. FR-41 의 "**잠재적으로 관심 있을 수 있는**" 의도를 회복.

**slot 1 (Fusion)**: 사용자 active trace × archived trace 의 cross-product 교차점. **(C-73 라운드 2026-06-11, [`../decisions.md §32`](../decisions.md))** — 2단 분리: **① 후보 생성 (deterministic)** `find_fusion_bridge_candidates` — 양 path 의 `long_score` DESC top_5 노드 교차쌍의 무방향 최단경로 내부 노드 ∪ 외향 bidirectional BFS (max_hops=3, 전 라운드) meet 노드, **깊이 ≥ `FUSION_BRIDGE_MIN_DEPTH=2` 필터** (cluster root=0 — C-53 min hop-sum 단독 선택이 root 허브로 100% 수렴하던 실측 결함 차단), (hop_sum ASC, depth DESC, uuid) 랭킹 상한 `FUSION_BRIDGE_CANDIDATES_MAX=8`. **② 선택 (LLM)** `fusion_select_llm.call_fusion_bridge_select` — medium slot, 닫힌 후보 목록에서 선택 또는 **명시 거부** (거부/실패/환각 모두 fusion_candidates=[] → trend fallback). `FUSION_BRIDGE_LLM_SELECT_ENABLED=false` 시 깊이 필터 1위 deterministic 모드. 후보 부재 시에도 trend fallback. (주: app cso_graph 는 hierarchy 엣지만 적재 — relatedEquivalent 는 1차 미사용, C-53 서술의 drift 를 본 라운드에서 정정.) `backend/app/traversal/fusion_bridge.py` + `backend/app/traversal/fusion_select_llm.py`. (C-53 의 meet-in-the-middle BFS 는 후보 생성 ①에 흡수 — [`../decisions.md §16`](../decisions.md) 결정 부분 무효.)

**(C-54 라운드 2026-05-24, [`../decisions.md §17`](../decisions.md))** — bridge_cso 결정 직후 같은 cron 안에서 **LLM web_search 도구로 bridge 영역 fresh Document 1~5건 fetch + DocumentTopic INSERT (bridge_cso 단일 매핑)**. bridge 가 valid 해도 매핑 Document 0개 = 빈 풀 risk 를 해소. 사용자 결정: prompt context = bridge_label + 두 path 라벨 + 각 trace 최근 saved Document 제목 3개 (B2) + 직전 30일 fusion 카드 URL/title 회피 hint (P1). 기존 `provider.search_with_tools` 인터페이스 + collection prompt 재사용 (C1). 실패 시 fusion_candidates 보존 + INSERT 0건 → dashboard 빈 풀 fallback trend (F1). `backend/app/profile/fusion_fetch.py:fetch_fusion_documents`.

```
profile = await get_user_profile(user_id)
# slot 1 — Fusion
for candidate in profile.fusion_candidates:
    bridge_id = UUID(candidate.bridge_cso_topic_id)
    if bridge_id in cso_graph:
        pool += await query_discovery_fusion(user_id, bridge_id)
        break
# fallback 1 — broadening_seeds[0]
if not pool and profile.broadening_seeds:
    pool += await query_discovery_fusion(user_id, profile.broadening_seeds[0].cso_topic_id)
```

**slot 2 (Reincarnation)**: `score_tail >= 0.6` archived trace 의 path 끝 노드 + 산하 archived leaf 부활. Serendipity 3-dim framework (RecSys '25) 의 "taste reincarnation" — "강한 신호로 종료된 영역에서 다시 흥미 자료 제시". **(C-53 라운드 2026-05-24)** — `get_top_archived_trace` (deterministic top-1) → **`softmax_sample_trace`** (T=`REINCARNATION_SAMPLING_TEMPERATURE=0.3` default) 교체. 매일 다양한 archived trace — "매일 새 발견" 본질 정합. **(C-53 followup, 2026-05-24)** — 함수명 `softmax_sample_archived_trace` → `softmax_sample_trace` rename (active_trace 도 같은 기준 softmax — Fusion 의 active trace 선택도 매일 다양). `backend/app/profile/sampling.py:softmax_sample_trace`.

```
archived_trace = await get_top_archived_trace(user_id,
    score_tail_min=0.6, gap_days_min=7, current_active_day=user.active_day_counter)
if archived_trace:
    archived_leaves = await get_descendant_archived_leaves(user_id, trace=archived_trace)
    pool += await query_discovery_reincarnation(
        user_id, archived_trace.path[-1], [lf.leaf_topic_id for lf in archived_leaves])
# fallback 2 — deepening_seeds[0]
elif profile.deepening_seeds:
    pool += await query_discovery_fusion(user_id, profile.deepening_seeds[0].cso_topic_id)
```

**fallback 3 (Trend)**: 모든 경로 빈 list 시 — cold-start 직후 또는 archive 0건 신규 사용자.

```
if not pool:
    pool = await query_discovery_trend(user_id, list(trace_path_csos))
```

**UserProfile schema** (`backend/app/db/models/user_profile.py` + `data/schema.md` UserProfile §, alembic 0007): 6 필드 — 3 자유 텍스트 (recent_signals / persistent_tendencies / likely_dislikes summary) + 3 JSONB array (fusion_candidates / deepening_seeds / broadening_seeds). daily 19 UTC LLM cron 이 생성·영속, ORM/schema 만 (endpoint·UI 부재 — 사용자 결정 #4).

**Anti-pattern 회피**: LLM hallucination (cso_graph 부재 bridge_cso_topic_id) 매핑 가드 + cache-before-commit 회피 + per-user try/except + Lua atomic CAS release ([`decisions.md §15`](../decisions.md) 매트릭스).

**(C-53 라운드 2026-05-24) Promotion 메커니즘** — discovery/adjacent 카드의 강한 신호 (save) 시 core 부활:
- **Recommendation origin metadata** (alembic 0010): `origin_type` ('reincarnation' | 'fusion' | NULL) + `origin_ref` (archived trace_id | bridge_cso_topic_id) 컬럼. `engine._persist_recommendations` 가 discovery sub-slot 카드 INSERT 시 영속화.
- **`weekly_promotion_job`** (`WEEKLY_PROMOTION_CRON="0 18 * * 0"` 일요일 18 UTC): 직전 7-day UserEvent.save → origin metadata JOIN. Reincarnation save → `trace.status: archived → active` (path 보존). Fusion save → 새 active trace INSERT (path=[bridge_cso]). dedup + idempotent + cache invalidate. LLM 호출 X (빠름). active cap 무제한 (사용자 결정).
- 사용자 디자인 의도: "discovery / adjacent 의 목적이 core 확대도 있다". `worker/jobs/weekly_promotion.py`.

## 신뢰도 임계 (`recommendation.toml`)

```toml
[slot_targets]
core = 5
adjacent = 3
discovery = 2
total = 10

[confidence_thresholds]
# 슬롯에 들어갈 자격 — 이 이하 후보는 강제 보충 대상이 아니라 다른 슬롯으로 대체
core_min_topic_match = 0.75      # 토픽 적합도
adjacent_min_topic_match = 0.55
discovery_min_topic_match = 0.30
discovery_required_trust_level = "high"   # discovery는 trust_level=high만

[freshness]
# Document.published_at 기준 wallclock(달력) 일수 — 사용자 active day 무관.
# 문서 자체의 신선도이므로 사용자 잠수 여부와 독립.
# (C-51 baseline default, 2026-05-24) 미지정 slot fallback. core 와 동일.
# 24시간 이내 1.0, 30일 이상 0.3 floor 로 선형 감쇠.
fresh_full_hours = 24
fresh_floor_after_wallclock_days = 30
fresh_floor_value = 0.3

[freshness.core]
# (C-51) 안정성 우선. 30일 후 floor 0.3.
fresh_full_hours = 24
fresh_floor_after_wallclock_days = 30
fresh_floor_value = 0.3

[freshness.adjacent]
# (C-51) 좀 더 fresh 우선. 14일 후 floor 0.2.
fresh_full_hours = 24
fresh_floor_after_wallclock_days = 14
fresh_floor_value = 0.2

# (C-53 followup, 2026-05-24) [freshness.discovery] sub-table 폐기 — discovery 는
# 코드 상수 `_UNITY_FRESHNESS` (factor 1.0 강제) 으로 처리. discovery = 매일 새 발견 +
# Reincarnation 본질 (decay 의미 X). config_loader.freshness_for_slot("discovery") 분기 참조.

[trust_level_weights]
high = 1.0
medium = 0.85
low = 0.6

[ranking_weights]
# 최종 점수 = topic_match * w_match + freshness * w_fresh + trust * w_trust
# (C-51, 2026-05-24) w_fresh 0.2 → 0.35 강화 — 사용자 의도 "최신성 추천 핵심" 부합.
w_match = 0.55
w_fresh = 0.35
w_trust = 0.1

[diversification]
# 동일 source에서 슬롯당 최대 N개
max_per_source_in_slot = 2
# 동일 leaf_topic에서 슬롯당 최대 N개
max_per_leaf_in_slot = 3

[core_slot_quota]
# C-4 해소: emerging leaf 우선 배치 (cso-topic-traversal.md §6.2)
emerging_leaf_quota_in_core = 1     # core 5개 중 1개는 emerging 우선, 후보 부재 시 active로 회수
```

## 랭킹 점수

```
score(d, u) = topic_match(d, u) * w_match
            + freshness(d)      * w_fresh
            + trust(d.source)   * w_trust
```

- `topic_match(d, u)` = max over (cso, leaf) ∈ d.topics of `bucket_score(u, topic) * d.topic_confidence`
  - bucket_score: high=1.0, medium=0.7, low=0.4, neutral=0.2
- `freshness(d)`: hours_since_publish 기준 선형 감쇠 (위 TOML)
- `trust(s)`: source.trust_level → 가중치

NFR-06에 의해 `topic_match`가 1순위 정렬 기준. 동률은 freshness, trust 순.

## Fallback 룰

### FR-42: 슬롯 후보 부족 (저신뢰 강제 X)

특정 슬롯의 신뢰도 임계 충족 후보가 목표 미달이면, 같은 신뢰도 임계를 만족하는 다른 슬롯 후보로 대체. 예: core 4개만 가능하면 1개를 adjacent에서 추가.

```python
def fill_with_fallback(slots, candidates, targets):
    filled = {s: [] for s in slots}
    for slot in slots:
        ok = [c for c in candidates[slot] if c.score >= thresholds[slot]]
        filled[slot] = ok[:targets[slot]]
    deficits = {s: targets[s] - len(filled[s]) for s in slots}
    total_deficit = sum(deficits.values())
    if total_deficit == 0:
        return filled
    # FR-42: 다른 슬롯의 잉여(또는 추가 후보)로 대체
    for slot, deficit in deficits.items():
        if deficit <= 0:
            continue
        donors = sorted(slots, key=lambda s: -len(candidates[s]))
        for donor in donors:
            if donor == slot:
                continue
            extras = [c for c in candidates[donor]
                      if c not in filled[donor] and c.score >= thresholds[slot]]
            take = extras[:deficit]
            for t in take:
                filled[slot].append(t.with_marker(slot_type=f"fallback_{donor}", fallback_reason=f"slot_{slot}_short_by_{deficit}"))
            deficit -= len(take)
            if deficit == 0:
                break
        # 여전히 부족해도 저신뢰 후보를 강제하지는 않음 (FR-42)
    return filled
```

### FR-43: 전체 후보 < 10

전체 후보가 10보다 적으면 다음 다단계 fallback 적용:

1. **인접 토픽 트렌드** — `topic-engine.find_adjacent(user_topics, hops=2)` 범위에서 trust_level=high 문서.
2. **신뢰 소스 전체 트렌드** — 사용자 토픽 무관, trust_level=high 학술/벤더 + 낚시성 통과 뉴스 중 최근 7일(wallclock) 인기 문서.
3. **신뢰 소스 archive** — 트렌드도 부족하면 trust_level=high 소스의 최근 30일.

각 단계는 부족한 만큼만 채우고, RecommendationSlot 행에 `slot_type=fallback_trend`, `fallback_reason="overall_short"` 기록.

## 다양성 룰

같은 슬롯 안에서:

- 동일 source 최대 2개 (`max_per_source_in_slot`)
- 동일 leaf_topic 최대 3개

이를 위해 후보 정렬 후 greedy diversification 적용:

```python
def diversify(candidates, max_per_source, max_per_leaf):
    selected = []
    src_count = defaultdict(int)
    leaf_count = defaultdict(int)
    for c in candidates:
        if src_count[c.source_id] >= max_per_source:
            continue
        if c.leaf_topic_id and leaf_count[c.leaf_topic_id] >= max_per_leaf:
            continue
        selected.append(c)
        src_count[c.source_id] += 1
        if c.leaf_topic_id:
            leaf_count[c.leaf_topic_id] += 1
    return selected
```

## 의사 코드 (전체 흐름)

```python
async def build_dashboard(user_id, ctx) -> DashboardResult:
    if await is_cold_start(user_id):
        return await cold_start_dashboard(user_id, ctx)   # cold-start.md

    user_topics = await ctx.interest.list_topics(user_id, with_bucket=True)
    candidates = {
        "core": await query_core(user_id, user_topics),
        "adjacent": await query_adjacent(user_id, user_topics),
        "discovery": await query_discovery(user_id, user_topics),
    }
    for slot in candidates:
        candidates[slot] = score_and_sort(candidates[slot], user_topics, ctx.config.ranking_weights)
        candidates[slot] = diversify(candidates[slot], ctx.config.diversification)

    filled = fill_with_fallback(candidates, ctx.config.thresholds, ctx.config.slot_targets)
    total = sum(len(v) for v in filled.values())
    if total < 10:
        # FR-43
        extra_needed = 10 - total
        trend = await build_trend_fallback(user_id, extra_needed, ctx)
        filled["fallback_trend"] = trend
    cards = serialize_cards(filled, user_topics, ctx.llm)
    cards = generate_reasons(cards, ctx.llm)        # 한국어 1문장, FR-44
    persist_recommendations(user_id, cards, filled)
    return DashboardResult(cards=cards, slots=summary_slots(filled))
```

## 추천 이유 (`reason_short`)

문서 상세에서 보일 짧은 토픽 근거 (FR-52, NFR-03). LLM `medium` 슬롯으로 1회 호출 (배치 가능).

System: "당신은 사용자에게 추천 이유를 한국어 한 문장으로 설명하는 어시스턴트다. 점수, 모델, 알고리즘은 언급하지 않는다. 토픽 라벨과 출처만 자연어로 활용한다."

User: "토픽: {label}. 출처: {source}. 슬롯: {slot}."

응답 검증: 길이 ≤ 80자. 점수/모델 키워드 포함 금지 (`bucket`, `score` 등 거부).
