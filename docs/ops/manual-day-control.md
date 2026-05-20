# Manual day-by-day control — `scripts/simulate_user_day.py`

수동 day 진전 + worker job trigger 도구. 시연 narrative ("다음 날이 됐다고 가정"), P1-12 fix 검증, 디버그용. backend daily 18 UTC cron 을 기다리지 않고 같은 job 함수를 직접 호출해서 시간 가속.

본 문서 + `backend/scripts/simulate_user_day.py` + Makefile 의 `next-day` / `collection-user` / `weekly-user` 3 target 이 한 묶음. 새 세션에서 진입 시 본 파일 1장만 읽고도 사용 가능하게 설계.

---

## 의도

실제 사용자가 Electron client (또는 API 직접) 로 카드 click / save / hide / not_interested / view + dwell 자유롭게 행동 → `user_event` 가 backend 로 들어가면 `ingest_event_atomic` 이 `active_day_at_event` 자동 채움. **이후 본 도구로 "다음 날" 명령** → backend 가 24h (실제로는 active day 7-day 누적) 시그널 평가 → `evaluate_extend` / `evaluate_split` 자동 호출 → trace.path 확장 + recommendation cache invalidate → 다음 dashboard GET 에서 새 ranking.

cron 자연 흐름:
- `interest_decay_job` (`INTEREST_DECAY_CRON`, 0 18 * * *) — 활성 day delta 기반 Bayesian 감쇠
- `daily_lifecycle_evaluation_job` (`TRACE_MERGE_CRON`, 0 18 * * *) — **P1-12 fix 후**: extend / split 평가 + retract / archive / leaf 강등
- `collection_job` (`COLLECTION_CRON`, 0 3 * * *) — LLM tool-use web_search 신규 doc 수집
- `leaf_lifecycle_job` / `trace_merge_job` / `user_profile_generation_job` — 각각 cron 따로

본 도구는 위 cron 호출의 명시 wrapper. 같은 함수, 같은 user-mutex, 같은 cache invalidate.

---

## 명령 3종

| 명령 | Make target | 효과 | 소요 |
|---|---|---|---|
| `next-day` | `make next-day` | `user.active_day_counter += 1`, `last_active_calendar_date = NULL` → `interest_decay_job` → `daily_lifecycle_evaluation_job` (P1-12 extend/split caller 포함) | ~5s |
| `collection` | `make collection-user` | 단일 user 대상 `collection_job(user_id_str)` — LLM web_search 1회. 신규 real document INSERT | 60~180s |
| `weekly` | `make weekly-user` | `leaf_lifecycle_job` + `trace_merge_job` + `user_profile_generation_job` (LLM 3~4회) | 60~180s |

---

## 사용

### Makefile 경로 (권장)

```bash
# repo root 에서 — EMAIL 또는 USER_ID 중 하나 명시
make next-day EMAIL=user@example.com
make collection-user EMAIL=user@example.com
make weekly-user USER_ID=fd81e703-7cb0-44fc-a708-fa83a756ad77
```

### 컨테이너 직접 호출

```bash
docker compose exec api python -m scripts.simulate_user_day next-day \
    --user-email user@example.com

docker compose exec api python -m scripts.simulate_user_day collection \
    --user-id fd81e703-7cb0-44fc-a708-fa83a756ad77

docker compose exec api python -m scripts.simulate_user_day weekly \
    --user-email user@example.com
```

`--user-email` 과 `--user-id` 는 둘 중 하나만. 둘 다 주면 `user-id` 우선.

### 호스트 native python (드물게)

`backend/` 디렉토리에 venv 활성화 + `DATABASE_URL` 환경변수 채운 상태에서 `python -m scripts.simulate_user_day ...`. 단 worker job 안의 redis 연결이 docker network 안 `redis://redis:6379` 가정이라 호스트 native 는 `REDIS_URL=redis://127.0.0.1:6379/0` 같은 override 필요. **권장은 docker compose exec**.

---

## 출력 예

`make next-day EMAIL=user@example.com`:

```
[before] ad=92 traces=5 leaves=6 events=265 real_docs=43 max_path_len=2
[bump]   active_day_counter -> 93
  OK   interest_decay_job: INFO:interest_decay_job:interest_decay_job: users_processed=1
  OK   daily_lifecycle_evaluation_job: INFO:daily_lifecycle_evaluation_job:daily_lifecycle_evaluation_job extended=1 split=0 retracted=0 archived=0 leaf_demoted=0
[after]  ad=93 traces=5 leaves=6 events=265 real_docs=43 max_path_len=3
```

핵심 신호:
- **`extended=N`** — N 개 trace 가 path.append 자동 진전 (자식 cso 임계 통과)
- **`split=N`** — N 개 trace 가 동시 부상 자식 둘 보고 fork (T 단축 + T' 신규)
- **`max_path_len` 증가** — trace.path 가 root → child → grandchild 로 깊어진 시각적 확인
- **`retracted=N` / `archived=N`** — stale 누적 trace 자연 강등
- **`leaf_demoted=N`** — emerging/active leaf 가 신호 부족으로 stale/archived 전이

`extended=0 split=0` 가 매번 떨어지면 → "알려진 한계" §1 참고.

---

## 흐름 권장 (시연)

1. **Electron client 부트** — `cd client && npm start` (또는 docker compose 부트 후 `make dev`).
2. 사용자가 카드 click/save/hide 자유 인터랙션 — `user_event` 가 backend 에 누적. `cso_topic_id` / `leaf_topic_id` 는 client 가 카드의 topic chip 정보로 채워 보냄.
3. 채팅 또는 터미널에서 `make next-day EMAIL=user@x` — 자식 시그널 통과 시 path 자동 확장.
4. Electron 에서 dashboard 새로고침 (POST `/recommendations/dashboard/refresh`) — recommendation cache 가 daily_lifecycle 안에서 자동 invalidate 됐으니 새 ranking.
5. 며칠 누적 후 `make collection-user EMAIL=user@x` — LLM 이 새 trace tail (확장된 path 끝) 기준으로 web_search → 더 specific 자료 수집.
6. 주 1회 정도 `make weekly-user EMAIL=user@x` — emerging leaf 평가 + trace merge + user_profile (A8-v2 fusion seed) 갱신.

---

## 안전장치 (실패 없이 동작하는 이유)

- **fresh subprocess per job**: 한 Python process 안에서 `asyncio.run()` 을 연속 호출하면 redis async client 가 닫힌 loop 에 묶여 `RuntimeError: Event loop is closed` 발생 (시뮬레이션 첫 시도에서 발견된 결함). 본 도구는 각 worker job 을 별도 `subprocess.run(["python", "-c", ...])` 로 호출해 fresh process + fresh event loop 보장.
- **timeout per command**: `next-day` 60-120s, `collection` 300s, `weekly` 240s — codex_oauth 응답이 느려져도 그 잡만 TIMEOUT 으로 종료, 다른 잡 영향 X.
- **trace mutation lock**: `daily_lifecycle_evaluation_job` 안의 `traversal_lock` Redis mutex 가 ingest_event / trace_merge_job 와 동시 실행 차단 — race 없음.
- **idempotent active_day +1**: SQL 가드 `WHERE last_active_calendar_date IS NULL OR < :today`. 본 도구는 의도적으로 `NULL` 로 리셋해서 다회 호출 가능 — 같은 calendar day 안에 "다음 날" 여러 번 호출하면 매번 카운터 +1 (시연 의도).
- **재진입 안전**: 명령 실행 도중 끊겨도 partial state 무해. active_day 만 +1 된 채 daily_lifecycle 안 돌아도 다음 호출 시 정상 처리.
- **before/after 스냅샷**: 매 호출 시 사용자 상태 한 줄 출력 — 무엇이 바뀌었는지 한 눈에 확인.

---

## 알려진 한계

1. **extend trigger 의 자식 cso 시그널 의존**:
   `evaluate_extend` 가 호출되려면 `daily_lifecycle_evaluation._evaluate_trace_expansion_for_user` 의 query 가 자식 후보를 찾아야 함:
   ```sql
   WHERE ctp.parent_cso_topic_id = <trace.path[-1]>
     AND COUNT(DISTINCT user_event 매핑 doc) >= TRACE_EXTEND_MIN_INTERACTIONS (5)
     AND (user.active_day_counter - active_day_at_event) <= 7
   ```
   사용자 trace path 끝 cso 의 그래프 자식 cso 에 매핑된 document 가 7 active day 안에 5건 이상 click/save/view/etc 받아야 trigger. **doc 매핑이 root cso 만이면 영원히 extend 안 됨** — collection 결과 매핑이 root 자체일 때 발생. 시뮬레이션/디버그 시 `document_topic` 에 child cso 매핑 행 수동 INSERT 가능 (P1-12 PR sim2/sim3 setup 참고).

2. **`execute_archive` / `evaluate_archive` caller 도 P1-12 잔여**:
   본 PR 의 extend/split caller fix 와 별개. `archive_if_eligible` 만 `_evaluate_trace_demotion_for_user` 안에서 호출되는 형태. 후속 PR 영역.

3. **CSO 그래프 cycle**:
   일부 자식 cso 가 사이클 — `evaluate_extend` 의 atomic SQL 가드 (`array_position` 검사) + caller 의 `if child_cso in path: continue` 로 path 안에 이미 있는 노드는 skip. 즉 무한 루프 안 일어남.

4. **collection 의 새 doc dedup**:
   같은 trace tail 기준 LLM web_search 가 같은 자료 반복 → dedup (URL/DOI/제목 정규화) 으로 흡수. 매주 호출해도 신규 doc 증가 폭이 점차 줄 수 있음 — path 확장으로 tail 이 바뀌면 다시 새 자료 다양 들어옴.

5. **ChatGPT 5h 세션 한도** (`codex_oauth` provider 사용 시):
   `collection` / `weekly` LLM 호출이 한도 도달 시 codex CLI 가 `exit=1 stderr=''` 같은 silent fail. `make codex-status` 로 확인. fallback 으로 `.env` 의 `LLM_PROVIDER=openai` + `OPENAI_API_KEY` 사용 가능.

---

## 디버그

| 증상 | 확인 |
|---|---|
| `extended=0 split=0` 매번 | `psql -c "SELECT ctp.parent_cso_topic_id, count(*) FROM cso_topic_parent ctp JOIN document_topic dt ON dt.cso_topic_id = ctp.cso_topic_id GROUP BY 1 ORDER BY 2 DESC LIMIT 20"` — 자식 매핑 분포 |
| `collection` 후 real_docs 그대로 | `docker compose logs api --tail 50 \| grep collection` — LLM 호출 + dedup 결과 |
| `interest_decay` SQL error | `last_decay_active_day NULL` 케이스 — P1-12 PR 이 `COALESCE` 로 fix, 0009 후 정상 |
| `next-day` 가 5s 보다 오래 걸림 | daily_lifecycle 안 `_evaluate_trace_expansion_for_user` 의 자식 SELECT 가 무거움 (CSO 14k 그래프) — graph 캐시 hit 미스 가능, `docker compose restart api` 후 재시도 |
| `make next-day` 가 `[ERR] EMAIL=...` | env var 미설정 — `EMAIL=user@example.com` 한 줄에 같이 넣었는지 확인 |

worker log streaming:
```bash
docker compose logs -f worker
```

---

## 출처 / 변경 이력

- 신규 추가: 2026-05-20 (P1-12 fix PR — `A9-after-fix-extend`).
- 결함 발견 경위: `decision-backlog.md` §P1-12 (C-45 라운드).
- 관련 SOR: `docs/algorithms/cso-topic-traversal.md` §3 (extend/retract/split/archive 룰), `decisions.md` §4 (active day 시간 모델), `AGENTS.md` §에이전트 분할표 A7 항목 (부분 완료 표기).
