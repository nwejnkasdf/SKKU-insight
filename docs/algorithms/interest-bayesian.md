# 알고리즘: 베이지안 관심 추론 (Beta-Bernoulli)

본 파일은 SKKU InSight의 사용자 관심 상태 추론 알고리즘을 정의한다. SRS Open Issue 1·2를 해결한다. 관련 FR: FR-12, FR-17, FR-18, FR-19, FR-20. 관련 NFR: NFR-04, NFR-05, NFR-06.

> **Active day 기반 시간 단위**: 본 문서의 모든 시간 감쇠는 wallclock 일수가 아니라 **active day**(사용자 인터랙션 1+건 있는 날의 단조증가 카운터)를 단위로 한다. 사용자가 시험기간 등으로 잠수한 동안에는 감쇠가 적용되지 않아 자연스러운 reactivation이 가능하다. 자세히는 [`cso-topic-traversal.md §5`](cso-topic-traversal.md).

> **Trace propagation**: leaf에 대한 인터랙션은 leaf의 부모 cso_topic_id에 직접 가산되고, 동시에 [`cso-topic-traversal.md §4`](cso-topic-traversal.md)의 propagation 룰에 따라 trace 활성 path 위 조상 노드 점수에도 1-hop 0.5 감쇠로 가산된다.

## 개요

각 (사용자, 토픽) 쌍에 대해 두 개의 Beta 사후 분포를 유지한다.

- `short_posterior` — 단기 관심 (반감기 t1/2_short = 7 active days)
- `long_posterior` — 장기 관심 (반감기 t1/2_long = 60 active days)

각 분포는 `Beta(alpha, beta)` 형태이며 사용자가 토픽에 보이는 행동을 베르누이 시행으로 모델링한다 (관심=성공, 비관심=실패).

UserInterestState 테이블에는 `long_score`, `short_score`로 사후 평균 `alpha / (alpha + beta)`를 기록한다. 이 값은 NFR-04에 따라 일반 사용자 화면에 직접 노출하지 않으며, API는 bucket(high/medium/low/neutral)으로만 반환한다.

## 수식

### 1. 사후 업데이트 (이벤트 시 실시간)

각 이벤트는 `event_weights.toml`의 가중치 `w`와 토픽 분배 `p_i`를 갖는다.

```
w_i = w * p_i

if w_i > 0:                 # 관심 신호
    short_alpha += w_i
    long_alpha += w_i
elif w_i < 0:               # 비관심 신호
    short_beta += |w_i|
    long_beta += |w_i|
```

`p_i`는 이벤트 대상 문서의 `DocumentTopic.confidence`를 정규화 (합 = 1).

### 2. 시간 감쇠 (active day 기반)

half-life을 lambda로 변환:

```
lambda_short = ln(2) / 7      # 단기 감쇠율 (per active day)
lambda_long  = ln(2) / 60     # 장기 감쇠율 (per active day)
```

**감쇠는 wallclock 일수가 아니라 active day 차이만큼 적용된다.** 사용자가 그날 active(인터랙션 1+건)인 경우, 매 사용자별 일일 감쇠 잡 (또는 첫 인터랙션 시 lazy decay)에서 다음을 적용:

```
delta = user.active_day_counter - state.last_decay_active_day
if delta > 0:
    decay_short = exp(-lambda_short * delta)
    decay_long  = exp(-lambda_long * delta)
    state.short_alpha = alpha_prior + (state.short_alpha - alpha_prior) * decay_short
    state.short_beta  = beta_prior  + (state.short_beta  - beta_prior)  * decay_short
    state.long_alpha  = alpha_prior + (state.long_alpha  - alpha_prior) * decay_long
    state.long_beta   = beta_prior  + (state.long_beta   - beta_prior)  * decay_long
    state.last_decay_active_day = user.active_day_counter
```

prior로 회귀하므로 오래된 신호는 자연 소멸하고, 신선한 신호가 강하게 반영된다. 사용자가 14일 잠수한 후 active day 차이가 0이면 감쇠 미적용 — 사용자가 돌아왔을 때 이전 점수가 보존되어 reactivation이 자연스럽다.

### 3. 점수와 bucket 매핑

```
score_x = alpha_x / (alpha_x + beta_x)   # 사후 평균
```

bucket 룰:

| 조건 | bucket |
|---|---|
| `long_score >= 0.7 and short_score >= 0.6` | high |
| `long_score >= 0.5 or short_score >= 0.5` | medium |
| `long_score >= 0.3 or short_score >= 0.3` | low |
| 그 외 | neutral |

## 이벤트 → 우도 매핑

| event_type | weight (TOML 키) | 비고 |
|---|---|---|
| `view` (impression) | `view = 0.0` | 측정만, 사후 갱신 X |
| `click` | `click = +1.0` | |
| `dwell_tick` | `dwell_tick = +0.5` per 30s, 최대 4회 (≥2분에서 +2 효과) | SRS 2분 기준에 정렬 |
| `open_external` | `open_external = +2.0` | 원문 클릭은 강한 신호 |
| `save` | `save = +5.0` | 명시 긍정 |
| `hide` | `hide = -3.0` | 명시 부정 |
| `not_interested` | `not_interested = -5.0` | 강한 명시 부정 |

음수 가중치는 `beta`에 더해진다 (실패 카운트 증가).

## 토픽 분배 `p_i`

이벤트가 문서를 가리키면 `DocumentTopic`의 confidence를 정규화. 토픽 직접 지정 (예: not_interested) 이벤트는 단일 토픽에 100% 분배.

## 구성 파일 스키마

### `interest_params.toml`

```toml
# Bayesian Beta-Bernoulli prior (mild informative)
alpha_prior = 1.0
beta_prior = 4.0          # 비관심 쪽으로 약간 기운 prior — 콜드스타트에서 false-positive 줄임

# 시간 감쇠 (half-life, active days — wallclock 아님)
half_life_short_active_days = 7
half_life_long_active_days  = 60

# Onboarding prior boost (active day 기반)
onboarding_prior_boost = 1.0           # alpha_prior에 더함
onboarding_boost_active_days = 14      # 이 active day 후 boost 만료

# Trace 활성 조상 노드 propagation
propagation_hop_decay = 0.5
propagation_max_hops = 4
propagation_non_trace_ancestors = false   # trace 외 조상에는 전파 X

# Bucket 임계
bucket_high_long  = 0.70
bucket_high_short = 0.60
bucket_medium     = 0.50
bucket_low        = 0.30

# 감쇠 트리거
decay_apply_lazy = true                # 사용자 첫 일일 인터랙션 시 lazy decay (wallclock cron 아님)
```

### `event_weights.toml`

```toml
[weights]
view            = 0.0
click           = 1.0
dwell_tick      = 0.5
open_external   = 2.0
save            = 5.0
hide            = -3.0
not_interested  = -5.0

[caps]
dwell_tick_max_per_document = 4   # 30s * 4 = 2분 한도. SRS 체류 ≥ 2m 정합
weight_per_event_max = 5.0        # |w| > 5는 절대값 cap
```

## 의사 코드

> **동시성 가드**: read-modify-write 패턴은 동시 이벤트 시 lost update를 일으키므로 **atomic SQL increment**로 구현해야 한다. 자세히는 [`../sdd/concurrency.md §4.1`](../sdd/concurrency.md). 이벤트 batch buffer (concurrency §6)에서 dwell_tick은 5초 윈도우로 묶여 1회 atomic UPDATE로 합산 적용.

```python
async def ingest_event_atomic(event: UserEvent, weights: Weights, params: InterestParams, user: User):
    """동시 이벤트 race 방어를 위해 SQL atomic increment로 구현."""
    base_w = weights.lookup(event.event_type)
    if base_w == 0:
        return
    capped_w = clamp(base_w, -weights.caps.weight_per_event_max, weights.caps.weight_per_event_max)

    # NOTE: active_day_counter 갱신은 본 함수 진입 직전에 미들웨어/엔드포인트에서 이미 수행됨
    # (concurrency.md §4.2 maybe_increment_active_day). dwell_tick cap과 무관하게,
    # API에 이벤트가 도착한 시점에 사용자가 그날 활동했다는 신호로 카운트 +1 (idempotent).
    # cap에 걸려 베이지안 갱신이 무시되더라도 active_day는 이미 카운트되어 있음.

    if event.event_type == "dwell_tick":
        # atomic check-and-increment with cap
        ok = await session.execute(
            text("""
                INSERT INTO dwell_tick_count (user_id, document_id, count)
                VALUES (:user_id, :doc_id, 1)
                ON CONFLICT (user_id, document_id)
                DO UPDATE SET count = dwell_tick_count.count + 1
                WHERE dwell_tick_count.count < :cap
                RETURNING count
            """),
            {"user_id": event.user_id, "doc_id": event.document_id, "cap": weights.caps.dwell_tick_max_per_document},
        )
        if ok.first() is None:
            return  # cap 도달, 베이지안 갱신 skip (단 active_day는 이미 +1 됨)

    topic_distribution = await resolve_topic_distribution(event)  # sum to 1
    for topic_id, p_i in topic_distribution.items():
        w_i = capped_w * p_i
        if w_i > 0:
            da_s, db_s, da_l, db_l = w_i, 0, w_i, 0
        else:
            da_s, db_s, da_l, db_l = 0, -w_i, 0, -w_i

        # 단일 atomic UPDATE — concurrency.md §4.1 권장 패턴
        await session.execute(
            text("""
                INSERT INTO user_interest_state (state_id, user_id, cso_topic_id, leaf_topic_id,
                                                 short_alpha, short_beta, long_alpha, long_beta,
                                                 short_score, long_score,
                                                 last_event_active_day, last_decay_active_day, updated_at)
                VALUES (gen_random_uuid(), :user_id, :cso_id, :leaf_id,
                        :alpha_p + :da_s, :beta_p + :db_s, :alpha_p + :da_l, :beta_p + :db_l,
                        (:alpha_p + :da_s) / NULLIF(:alpha_p + :da_s + :beta_p + :db_s, 0),
                        (:alpha_p + :da_l) / NULLIF(:alpha_p + :da_l + :beta_p + :db_l, 0),
                        :active_day, :active_day, NOW())
                ON CONFLICT (user_id, cso_topic_id, leaf_topic_id)
                DO UPDATE SET
                    short_alpha = user_interest_state.short_alpha + :da_s,
                    short_beta  = user_interest_state.short_beta  + :db_s,
                    long_alpha  = user_interest_state.long_alpha  + :da_l,
                    long_beta   = user_interest_state.long_beta   + :db_l,
                    short_score = (user_interest_state.short_alpha + :da_s) /
                                  NULLIF(user_interest_state.short_alpha + :da_s + user_interest_state.short_beta + :db_s, 0),
                    long_score  = (user_interest_state.long_alpha + :da_l) /
                                  NULLIF(user_interest_state.long_alpha + :da_l + user_interest_state.long_beta + :db_l, 0),
                    last_event_active_day = :active_day,
                    updated_at = NOW()
            """),
            {"user_id": event.user_id, "cso_id": topic_id.cso, "leaf_id": topic_id.leaf,
             "alpha_p": params.alpha_prior, "beta_p": params.beta_prior,
             "da_s": da_s, "db_s": db_s, "da_l": da_l, "db_l": db_l,
             "active_day": user.active_day_counter},
        )


def daily_decay(params: InterestParams, state_store: StateStore) -> None:
    decay_short = math.exp(-math.log(2) / params.half_life_short_active_days)
    decay_long  = math.exp(-math.log(2) / params.half_life_long_active_days)
    for s in state_store.iter_all():
        s.short_alpha = params.alpha_prior + (s.short_alpha - params.alpha_prior) * decay_short
        s.short_beta  = params.beta_prior  + (s.short_beta  - params.beta_prior)  * decay_short
        s.long_alpha  = params.alpha_prior + (s.long_alpha  - params.alpha_prior) * decay_long
        s.long_beta   = params.beta_prior  + (s.long_beta   - params.beta_prior)  * decay_long
        s.updated_at = now()
        state_store.put(s)


def bucket_for(state: UserInterestState, params: InterestParams) -> Bucket:
    long_score = state.long_alpha / (state.long_alpha + state.long_beta)
    short_score = state.short_alpha / (state.short_alpha + state.short_beta)
    if long_score >= params.bucket_high_long and short_score >= params.bucket_high_short:
        return Bucket.HIGH
    if long_score >= params.bucket_medium or short_score >= params.bucket_medium:
        return Bucket.MEDIUM
    if long_score >= params.bucket_low or short_score >= params.bucket_low:
        return Bucket.LOW
    return Bucket.NEUTRAL
```

<!-- TODO: A6가 토픽 분배 시 leaf_topic이 없는 이벤트 처리 정책 확정 -->
