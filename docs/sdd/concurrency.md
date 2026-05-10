# 동시성·부하 가드 (Concurrency Specification)

본 파일은 SKKU InSight가 **10-20명 동시 사용자** 부하에서 정합성과 NFR-12(p95 3초)를 보장하기 위한 모든 동시성 가드 패턴을 한 곳에 정리한다. 후속 에이전트는 코드 작성 시 본 문서의 패턴을 그대로 적용한다. 관련: [`../api/recommendation.md`](../api/recommendation.md), [`../algorithms/interest-bayesian.md`](../algorithms/interest-bayesian.md), [`../algorithms/cso-topic-traversal.md`](../algorithms/cso-topic-traversal.md), [`../ops/env-vars.md`](../ops/env-vars.md).

## 운영 가정

- **동시 활성 사용자**: 10-20명 (피크: 09:00 KST 같은 시간대 첫 접속, 발표 시연)
- **인터랙션 빈도**: 사용자당 평균 분당 5-10건 (click/dwell_tick/save/hide)
- **dwell_tick 폭증 가능**: 카드 4분 시청 시 dwell_tick 8건 발생
- **신규 가입 burst**: 발표 시연 시 동시 가입 3-5명 가능 → cold-start LLM 호출 burst
- **단일 docker compose 머신**: 수직 확장 없음, 단일 Postgres + Redis + api + worker

## 1. DB 연결 풀 분리

api와 worker가 같은 풀을 공유하면 worker가 긴 작업 동안 connection 잡고 있으면 api 요청 대기.

```
PG_API_POOL_MIN = 5
PG_API_POOL_MAX = 30        # 사용자 20명 + 폴링/캐시 갱신 여유
PG_WORKER_POOL_MIN = 2
PG_WORKER_POOL_MAX = 10     # 수집/lifecycle/병합 잡 동시
```

`docker-compose.yml`에서 api와 worker가 `DATABASE_URL`은 공유하되 application 레벨에서 풀을 분리. 환경변수 [`../ops/env-vars.md`](../ops/env-vars.md) 참고.

Postgres 자체의 `max_connections`은 100 default. 위 풀 + admin-console + 수동 query 여유로 충분.

## 2. Recommendation 캐시 single-flight (stampede 방어)

20명 동시 첫 접속 시 모두 캐시 miss → 20개 동시 `build_dashboard` → DB pool + LLM 호출 burst. 사용자당 in-flight build를 1개로 dedup.

### 패턴

```python
# app/recommendation/service.py
async def get_dashboard(user_id: UUID) -> DashboardResponse:
    # 1. 캐시 hit
    cached = await redis.get(f"recommendation:{user_id}")
    if cached:
        return DashboardResponse.parse_raw(cached)

    # 2. single-flight Redis lock
    lock_key = f"lock:recommendation_build:{user_id}"
    lock = await redis.set(lock_key, "1", nx=True, ex=30)  # 30s TTL
    if not lock:
        # 다른 요청이 build 중. 짧은 폴링으로 캐시 결과 대기 (최대 8초)
        for _ in range(40):
            await asyncio.sleep(0.2)
            cached = await redis.get(f"recommendation:{user_id}")
            if cached:
                return DashboardResponse.parse_raw(cached)
        # 8초 초과 — 폴백: 직접 build
        return await _build_directly(user_id)

    try:
        # 3. lock 보유 — 본격 build
        result = await build_dashboard(user_id)
        await redis.setex(
            f"recommendation:{user_id}",
            CACHE_TTL_SECONDS,
            result.json(),
        )
        return result
    finally:
        await redis.delete(lock_key)
```

### TTL 정책

```
CACHE_TTL_SECONDS = 3600  # 1시간 또는 다음 collection cron까지 짧은 쪽
```

캐시 무효화는 `save`/`hide`/`not_interested`/`refresh` 명시 액션에만. 단순 click·dwell은 캐시 유지하고 베이지안 비동기 갱신만 (H-5 결정).

## 3. User-level mutex (trace operation race 방어)

한 사용자의 동시 이벤트가 동일 trace에 race를 일으켜 path 더블 append 등 정합성 깨질 수 있음. **사용자당 1개의 trace mutation을 직렬화**.

### 패턴 — Redis lock per user

```python
# app/traversal/engine.py
async def ingest_event(user: User, event: UserEvent) -> TraversalDelta:
    lock_key = f"lock:traversal:{user.user_id}"
    # SET NX with timeout — 다른 in-flight 작업 대기 (최대 5초)
    deadline = time.time() + 5.0
    while True:
        ok = await redis.set(lock_key, "1", nx=True, ex=10)
        if ok:
            break
        if time.time() > deadline:
            raise TraversalLockTimeout(user.user_id)
        await asyncio.sleep(0.05)
    try:
        return await _ingest_event_locked(user, event)
    finally:
        await redis.delete(lock_key)
```

### 대안 — Postgres advisory lock

`pg_advisory_xact_lock(hashtext('traversal:' || user_id::text))`. Redis lock보다 정합성 강하지만 트랜잭션 종료까지 보유 — 코드는 더 간결. 1차는 Redis lock 권장.

### 적용 범위

- `TraversalEngine.ingest_event` — 모든 trace mutation 진입점
- `LifecycleEvaluator.evaluate_transitions` — 일일 leaf 평가
- 동의 철회 처리 — 사용자별 trace stale 마킹

## 4. Atomic SQL — 베이지안 + counter

### 4.1 베이지안 사후 update (H-1 해소)

read-modify-write 대신 atomic increment.

```python
# app/interest/service.py
async def update_posterior(state_id: UUID, delta_alpha_short: float, delta_alpha_long: float, ...):
    await session.execute(
        text("""
            UPDATE user_interest_state
            SET short_alpha = short_alpha + :da_s,
                short_beta  = short_beta  + :db_s,
                long_alpha  = long_alpha  + :da_l,
                long_beta   = long_beta   + :db_l,
                last_event_active_day = :active_day,
                short_score = (short_alpha + :da_s) / (short_alpha + :da_s + short_beta + :db_s),
                long_score  = (long_alpha + :da_l) / (long_alpha + :da_l + long_beta + :db_l),
                updated_at  = NOW()
            WHERE state_id = :state_id
        """),
        {"da_s": delta_alpha_short, "db_s": delta_beta_short,
         "da_l": delta_alpha_long, "db_l": delta_beta_long,
         "active_day": user.active_day_counter, "state_id": state_id},
    )
```

동시 N개 이벤트가 같은 state를 갱신해도 race 없음.

### 4.2 active_day_counter atomic (#4)

```python
async def maybe_increment_active_day(user_id: UUID, today: date) -> int:
    """오늘 첫 인터랙션이면 +1. idempotent."""
    result = await session.execute(
        text("""
            UPDATE "user"
            SET active_day_counter = active_day_counter + 1,
                last_active_calendar_date = :today
            WHERE user_id = :user_id
              AND (last_active_calendar_date IS NULL OR last_active_calendar_date < :today)
            RETURNING active_day_counter
        """),
        {"user_id": user_id, "today": today},
    )
    row = result.first()
    if row:
        return row.active_day_counter
    # 이미 오늘 카운트됨 — 현재 값 조회
    result = await session.execute(
        text("SELECT active_day_counter FROM \"user\" WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    return result.scalar_one()
```

`WHERE last_active_calendar_date < :today` 조건이 동시 두 요청 중 첫 번째만 통과시키는 가드. 두 번째는 row 0건 갱신.

## 5. LLM concurrent call cap (Redis 분산 semaphore, multi-worker 안전)

외부 API rate limit (OpenAI Tier 1 = 60 req/min, codex_oauth는 더 엄격) 보호 + 한 사용자 몰빵 방어.

### Worker 병렬 정책 (decision-backlog C-19, C-20)

`UVICORN_WORKERS>1` 또는 다중 컨테이너 환경에서 `asyncio.Semaphore` 는 process 별
독립이라 전역 캡 보장 불가 (워커 4개 × 8 = 실효 32). 따라서 **Redis 기반 분산
semaphore** 사용 — Lua atomic check + INCR/DECR + TTL.

### 구현 — Redis 분산 semaphore (Lua atomic)

```python
# app/llm_provider/_concurrency.py
_LUA_ACQUIRE = """
local global_limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[3])
local global_current = tonumber(redis.call('GET', KEYS[1]) or '0')
if global_current >= global_limit then return 0 end
if KEYS[2] ~= '' and ARGV[2] ~= '' then
    local user_limit = tonumber(ARGV[2])
    local user_current = tonumber(redis.call('GET', KEYS[2]) or '0')
    if user_current >= user_limit then return 0 end
    redis.call('INCR', KEYS[2])
    redis.call('EXPIRE', KEYS[2], ttl)
end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ttl)
return 1
"""

@asynccontextmanager
async def acquire_slot(user_id):
    redis = get_redis("default")
    deadline = time.monotonic() + settings.LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS
    backoff = 0.05
    while True:
        if int(await redis.eval(_LUA_ACQUIRE, 2, global_key, user_key, ...)) == 1:
            break
        if time.monotonic() >= deadline:
            raise LLMBudgetExceeded("semaphore_timeout")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 0.5)
    try:
        yield
    finally:
        await redis.eval(_LUA_RELEASE, 2, global_key, user_key)
```

### Redis 키

```
llm:active:global              INCR/DECR, TTL 60s
llm:active:user:{user_id}      INCR/DECR, TTL 60s (per-user cap)
```

- TTL 60s: acquire 후 release 못한 채 crash 시 자연 해소. LLM 단일 호출이 60s
  안에 끝난다 가정 (`LLM_REQUEST_TIMEOUT_SECONDS=60` 과 정합).
- DECR floor 0 (음수 방지 — Lua 안에서 check)

### 권장 cap

```
LLM_MAX_CONCURRENT = 8                          # 전역 동시 LLM 호출 cap
LLM_MAX_CONCURRENT_PER_USER = 2                  # 한 사용자 burst cap
LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS = 30      # acquire 재시도 한도
```

20명이 동시 cold-start 호출해도 8개씩 직렬화 — multi-worker 환경에서도 같은 보장.

### Mock provider 예외

`LLM_PROVIDER=mock`은 deterministic 즉답이라 acquire 우회 가능 (mock provider 가
`acquire_slot` 호출 안 함). OpenAI/Anthropic/OpenRouter 만 acquire.

## 6. Event batch flush (dwell_tick 폭증 완화)

dwell_tick 30초 단위 이벤트가 사용자당 분당 2건 → 20명이면 분당 40건 → DB write 부하 ↑.

### 패턴 — 5초 윈도우 batch insert

```python
# app/events/buffer.py
class EventBuffer:
    _buffer: list[UserEvent] = []
    _lock = asyncio.Lock()

    async def add(self, event: UserEvent):
        async with self._lock:
            self._buffer.append(event)
            if len(self._buffer) >= EVENT_BATCH_SIZE:
                await self._flush()

    async def flush_periodic(self):
        while True:
            await asyncio.sleep(EVENT_BATCH_FLUSH_SECONDS)
            async with self._lock:
                if self._buffer:
                    await self._flush()

    async def _flush(self):
        await session.execute(insert(UserEvent), [e.dict() for e in self._buffer])
        # 베이지안 update는 buffer flush 시점에 일괄 (atomic SQL §4.1)
        self._buffer.clear()
```

### 권장값

```
EVENT_BATCH_SIZE = 20
EVENT_BATCH_FLUSH_SECONDS = 5
```

API 응답은 buffer add 직후 즉시 반환 (idempotency를 client_request_id로 보장). 베이지안 갱신과 trace operation 검토는 flush 시 1회로 묶어 처리.

### 예외

`save`/`hide`/`not_interested`는 즉시 반영 (사용자 인지에 영향). dwell_tick / view / click만 batch 대상.

## 7. Consent active 캐시 (매 요청 DB hit 완화)

H-4 결정에 따라 모든 personalization endpoint 미들웨어가 consent 활성 검증. 매 요청 DB query는 부하 ↑.

### 패턴

```python
# app/consent/cache.py
async def is_consent_active(user_id: UUID) -> bool:
    cached = await redis.get(f"consent:active:{user_id}")
    if cached is not None:
        return cached == "1"
    active = await db_check_consent_active(user_id)
    await redis.setex(f"consent:active:{user_id}", 60, "1" if active else "0")
    return active

# 동의 철회 시점
async def revoke_consent(user_id: UUID):
    await db_revoke(user_id)
    await redis.delete(f"consent:active:{user_id}")  # 즉시 무효화
```

TTL 60초 — 사용자가 철회 후 최대 1분 동안 이전 상태로 작동 가능. 즉시 무효화 path는 명시 철회 endpoint에서 처리.

## 8. 일일 수집 잡 jitter

H-2: 사용자별 잡이 모두 03:00 UTC 동시 시작 시 외부 API thundering herd. 사용자별 시작 시각을 user_id 해시로 5분 jitter.

```python
# app/collection/scheduler.py
async def schedule_user_jobs(now: datetime):
    for user in active_users:
        offset_seconds = (hash(str(user.user_id)) % 300)   # 0~5분 분산
        await scheduler.add_job(
            run_user_collection,
            run_date=now + timedelta(seconds=offset_seconds),
            args=[user.user_id],
        )
```

20명이면 5분 윈도우에 4명/분으로 분산 → 외부 RL 안정.

## 9. NetworkX 캐시 read-only thread safety

NetworkX `DiGraph`는 read-only 사용 시 thread-safe (mutating 함수만 미보장). 본 시스템은 부팅 시 1회 build 후 read만. 단:

- CSO 재임포트 시 graph rebuild 필요 → api와 worker 컨테이너 모두 재시작
- runbooks.md §1 "topic_linkage 패턴 → make import-cso --refresh"에 명시

## 10. 운영 가드 종합 체크리스트

A2/A6/A7/A8 에이전트가 자기 모듈 코드 작성 시 본 표를 통과해야 한다.

| Endpoint / 작업 | 적용 가드 |
|---|---|
| `GET /recommendations/dashboard` | single-flight (§2) + consent cache (§7) |
| `POST /events` | event batch buffer (§6) — save/hide/not_interested 제외 즉시 |
| `POST /events` (저장/숨김/관심없음) | atomic SQL update (§4.1) + recommendation cache invalidate |
| `TraversalEngine.ingest_event` | user-level mutex (§3) |
| `interest-bayesian` posterior update | atomic SQL (§4.1) |
| Active day counter 갱신 | atomic SQL (§4.2) |
| 모든 LLM 호출 | semaphore (§5), `user_id` 전달 필수 |
| 일일 수집 잡 트리거 | jitter (§8) + user-level lock `RedisKey.collection_lock(user_id)` (동일 사용자 동시 1건 강제) |
| `POST /admin/collection/jobs/{id}/reprocess` | job_id 자체 idempotency — `RedisKey.collection_lock(user_id)` 또는 ReprocessRequest unique index (api-conventions.md §8) |
| consent middleware | Redis cache (§7) |
| CSO 재임포트 | api+worker 재시작 (§9) |

## 11. NFR-12 p95 3초 책임 구조

| 단계 | 목표 latency | 책임 가드 |
|---|---|---|
| 캐시 hit | < 50ms | §2 single-flight bypass |
| Recommendation build (cache miss, cold-start 아닌 경우) | < 1.5s | §1 pool, §4 atomic, §3 mutex |
| Cold-start LLM | < 8s (NFR-12 예외, 폴링) | §5 semaphore + §2 single-flight + 비동기 폴링 |
| `POST /events` (단순) | < 200ms | §6 batch buffer (즉시 응답) |
| `POST /events` (save/hide) | < 500ms | §4 atomic + cache invalidate |

## 12. 부하 테스트 시나리오 (A11 test-ci 작업 가이드)

```python
# tests/load/test_20_users.py
async def test_20_concurrent_dashboards():
    """20명 동시 대시보드 조회 → p95 3초 이하 + 정합성 보존."""
    users = await seed_personas(20)
    async with anyio.create_task_group() as tg:
        for u in users:
            tg.start_soon(get_dashboard_and_assert, u, deadline_seconds=3.0)

async def test_concurrent_events_same_user():
    """1명 사용자가 1초 내 10건 이벤트 → trace path 중복 append 없음 + 베이지안 lost update 없음."""
    user = await seed_persona()
    deltas = [make_click_event(user) for _ in range(10)]
    await asyncio.gather(*[ingest_event(user, e) for e in deltas])
    state = await get_state(user)
    assert state.long_alpha == pytest.approx(initial_alpha + 10 * click_weight)
    traces = await get_active_traces(user)
    assert all(len(t.path) == len(set(t.path)) for t in traces)
```

A11 작업 시 이 두 시나리오를 GitHub Actions matrix에 추가.
