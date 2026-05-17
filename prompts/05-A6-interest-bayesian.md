# A6 — Interest Bayesian (Phase 1) — ✅ 완료 (2026-05-17)

> 작업 디렉토리: ``
> **사전조건**: A2 backend + A3 cso-topic 완료. A4·A5와 병렬 가능.
> **상태**: ✅ [PR #18](https://github.com/nwejnkasdf/SKKU-insight/pull/18) merge `a0a3fbf` (2026-05-17). 본 prompt 는 kickoff input 보존용 (재실행 가이드). 실 결정·구현 변경은 [`docs/decisions.md §11`](../docs/decisions.md) + [`decision-backlog.md` C-37/C-38](../docs/decision-backlog.md) 참조 — 17건 결정 매트릭스 + Codex 2-라운드 audit 12 fix 가 본 prompt 작성 시점 이후 합의된 사항.

## 너의 역할

행동 로그 수집 + Beta-Bernoulli 사후 atomic UPSERT + active day 기반 시간 감쇠 + 1-hop trace 활성 path propagation.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `docs/algorithms/interest-bayesian.md` (전체)
- `docs/algorithms/cso-topic-traversal.md` §4 propagation, §5 active day
- `docs/data/schema.md` (UserInterestState, UserEvent, SavedDocument, HiddenDocument, NotInterestedTopic)
- `docs/api/interest.md`
- `docs/sdd/concurrency.md` §3 user-mutex, §4.1 atomic, §4.2 active_day, §6 batch flush

## 산출

### 1. `app/interest/` 모듈
- `service.py` — `ingest_event_atomic()` (interest-bayesian.md 의사 코드 그대로)
- `decay.py` — active day 기반 lazy decay (interest-bayesian.md §2)
- `bucket.py` — score → bucket(high/medium/low/neutral) 매핑 (NFR-04)
- `propagation.py` — 1-hop 0.5 propagation (cso-topic-traversal.md §4). trace 활성 path 위 조상에만, trace 외 조상 X. **A7 의존**, A7 미완료 시 propagation skip + 단순 단일 노드만 갱신.

### 2. atomic SQL UPSERT 패턴
- `concurrency.md §4.1` 의사 코드 그대로 — `INSERT ... ON CONFLICT DO UPDATE` 사용
- UserInterestState의 partial unique index (cso_only / leaf_only / pair) ↔ ON CONFLICT 매핑
- 동시 이벤트 race 방어

### 3. active_day_counter 갱신
- `app/user/active_day.py` — `maybe_increment_active_day(user_id, today)` atomic UPDATE (concurrency.md §4.2)
- 이벤트 도착 시점에 호출 (cap 도달과 무관)

### 4. dwell_tick cap
- `dwell_tick_count` 테이블 신규 (alembic migration) — 또는 Redis 카운터
- atomic INSERT ON CONFLICT WHERE count < cap

### 5. event batch flush
- `app/events/buffer.py` — `EventBuffer` (5초 윈도우, 20건 cap)
- click·view·dwell_tick batch, save·hide·not_interested 즉시
- 각 flush 후 베이지안 atomic UPSERT 일괄

### 6. Endpoint 본문
- `POST /events`, `POST /events/batch` — consent middleware + Redis user-lock (concurrency.md §3) + active_day 갱신 + buffer add
- `POST /feedback/save` (SavedDocument INSERT + 즉시 베이지안 update + recommendation cache invalidate)
- `POST /feedback/hide` (HiddenDocument INSERT)
- `POST /feedback/not-interested` (NotInterestedTopic INSERT, partial unique 패턴)
- `GET /interest/state` — bucket만 반환 (NFR-04 마스킹)

### 7. 시간 감쇠 (active day 기반)
- `interest_params.toml` 작성 — interest-bayesian.md 표 그대로
- lazy decay: 사용자 첫 일일 인터랙션 시 last_decay_active_day 와 차이만큼 적용 (concurrency.md §6에 정합)
- 또는 일일 cron (선택). 1차는 lazy 추천.

## 헌법 (재강조)

- **read-modify-write 패턴 금지**. 반드시 atomic SQL UPSERT.
- **propagation은 trace 활성 path 위에만** (cso-topic-traversal.md §4). trace 외 조상 propagate X. A7 미완료 시 단일 노드만 갱신.
- **score는 응답에서 절대 노출 X** (NFR-04). bucket만.
- **active_day는 dwell_tick cap과 독립** (이벤트 도착 시점에 갱신).

## 검증

```bash
docker compose up -d
# 사용자 1명 + 토픽 1개 시드
curl -X POST http://localhost:8000/events -d '{"event_type":"click","document_id":"...","client_request_id":"..."}' -H "Authorization: Bearer $TOKEN"

curl http://localhost:8000/interest/state -H "Authorization: Bearer $TOKEN"
# {topics: [{cso_topic_id, label, bucket: "low"}, ...]}

# 동시 race 검증
ab -n 100 -c 10 -p event.json -T application/json -H "Authorization: Bearer $TOKEN" http://localhost:8000/events
# UserInterestState.long_alpha 가 정확히 100×weight 만큼 증가했는지

mypy --strict backend/app/interest backend/app/events
ruff check
pytest backend/tests/interest -v
# 동시 100건 이벤트 → atomic 검증
# active_day_counter 동시 증가 idempotency
# dwell_tick cap 4 도달 후 무시 검증
# bucket 마스킹 검증
```

## 출력 형식

기본 + 추가:
- atomic UPSERT 동시 race 테스트 결과
- active_day_counter idempotency 테스트
- propagation 검증 (A7 stub 또는 단일 노드 모드)
- bucket 매핑 정확도
- 다음 Phase A7·A8가 봐야 할 결정
