# 운영 런북

본 파일은 SKKU InSight에서 자주 일어날 운영 시나리오에 대한 대응 절차를 정리한다. 시연 환경 + 개발 단계에서 발생할 수 있는 흔한 이슈만 다룬다. 관련: [`docker-compose.md`](docker-compose.md), [`env-vars.md`](env-vars.md), [`../security/threat-model.md`](../security/threat-model.md).

## 실행 환경 — Docker = WSL only (C-47, 2026-05-24)

본 문서의 모든 `docker` / `docker compose` / `make` (docker 호출 포함) 명령은 **반드시 WSL 안에서 실행**한다.

| 환경 | 결과 |
|---|---|
| WSL native docker engine (`unix:///var/run/docker.sock`, Docker Engine Community 29.x) | ✅ 정상 |
| PowerShell / git bash 에서 직접 `docker compose` | ❌ `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine` |
| Windows Docker Desktop Linux engine | ❌ 사용 안 함 (사용자 환경 기준) |

host file 접근은 `/mnt/c/Users/.../SKKU-InSight/...` path (WSL 의 windows mount). 컨테이너 이름은 worktree 별로 다름 — `docker ps -a` 로 확인 후 `wsl docker logs <container>`.

## 1. 일일 수집 잡 실패

### 증상
- 관리자 콘솔 `GET /admin/collection/jobs?status=failed`에 행이 누적
- NFR-10 (95%) 미달

### 진단
1. `docker compose logs worker | tail -200`에서 stack trace 확인
2. `failure_reason` 필드 패턴 분류:
   - `httpx.ConnectError` → 외부 소스 네트워크 이슈
   - `httpx.HTTPStatusError 429` → rate limit (소스가 우리를 차단)
   - `parser_error` → 어댑터 파서 버그 (소스 HTML 변경)
   - `clickbait_classifier_unavailable` → DoRA 컨테이너 다운
   - `topic_linkage_failed` → CSO 캐시 누락 또는 LLM 호출 실패

### 대응
| 패턴 | 대응 |
|---|---|
| 네트워크 일시 | 자동 retry (exponential backoff). 3회 실패 후 관리자 콘솔 표시. |
| 429 | 해당 Source `enabled=false`로 일시 끄기 + `extra.rate_limit_per_minute` 하향. |
| parser_error | 해당 어댑터 단위 테스트 추가 후 픽스. 임시로 Source `enabled=false`. |
| clickbait 다운 | `docker compose ps clickbait-detector` 확인. 재시작. |
| topic_linkage | NetworkX 캐시 reload (api 재시작) 또는 `make import-cso --refresh`. |

### 재실행

`POST /admin/collection/jobs/{job_id}/reprocess` (UC-05). 또는 일괄 처리:

```bash
docker compose exec api python -c "
from app.admin.reprocess import bulk_reprocess
import asyncio
asyncio.run(bulk_reprocess(status='failed', since_hours=24))
"
```

---

## 2. LLM rate limit / 토큰 예산 초과

### 증상
- `LLM_DAILY_TOKEN_BUDGET` 초과 → cold-start 실패, 추천 이유 미생성
- `Recommendation.reason` 컬럼 NULL 비율 증가

### 진단
1. `docker compose exec api python -m app.llm_provider.stats`로 토큰 사용량 출력
2. `LLM_PROVIDER` 확인 (codex_oauth는 짧은 세션 만료 가능)
3. provider별 메시지:
   - 401 → 토큰 만료 (Codex는 OAuth 세션 재발급, 다른 provider는 API 키 갱신)
   - 429 → rate limit
   - 5xx → provider 측 장애

### 대응
- 단기: `LLM_PROVIDER`를 다른 구현체로 토글 (예: openai → anthropic, 또는 mock 으로 fallback해 시연 끊김 방지). CodexOAuth는 local experimental이라 시연 환경 1차 default가 아니다 (`../sdd/architecture.md` LLM Adapter).
- 중기: `recommendation.toml`에서 `cold_start_max_per_day` 임시 cap 하향
- 장기: 토큰 예산 증액, 모델 슬롯 분리 (high → medium 으로 강제 다운그레이드)

### Fallback

LLM 실패 시:
- cold-start → trust_level=high trend로 대체 (`algorithms/cold-start.md`)
- 추천 이유 → "토픽 {label}와 관련" 식 템플릿 한국어 문장으로 대체
- leaf 식별 → 다음 day로 미루기 (해당 날짜 leaf 식별 skip)

---

## 3. DB 마이그레이션 실패

### 증상
- `alembic upgrade head` 실패
- api 컨테이너 재시작 루프

### 진단
```bash
docker compose exec api alembic current
docker compose exec api alembic history --verbose
docker compose exec api alembic upgrade head --sql > /tmp/migration.sql
```

### 대응
- 생성된 SQL이 합리적인지 검토
- 데이터 마이그레이션이 필요한 경우 `op.execute(...)` 추가
- 시연 환경에서는 `docker compose down -v` 후 처음부터 다시 (개발자 환경 한정)
- 운영(미래)은 백업 → downgrade → 패치 → upgrade

### 마이그레이션 단계 베스트 프랙티스
- 한 마이그레이션에 schema + data 변경을 섞지 말 것
- ALTER 이후 데이터 변환은 별도 마이그레이션
- INSERT/UPDATE 데이터 마이그레이션은 idempotent하게

---

## 4. Redis 메모리 / 큐 적체

### 증상
- `redis-cli info memory` 사용량 급증
- RQ queue 길이 증가 (`rq info`)

### 진단
- `redis-cli --bigkeys`로 큰 키 식별
- 가장 흔한 케이스: 추천 캐시 (`recommendation:{user_id}`) TTL 설정 안 됨

### 대응
- 캐시 키에 명시적 TTL (24h)
- RQ queue가 길면 worker concurrency 증설 (`COLLECTION_GLOBAL_CONCURRENCY` 상향) 또는 worker 컨테이너 N개 띄우기
- 초기화: `docker compose exec redis redis-cli flushdb` (개발 한정)

---

## 5. Electron 앱이 백엔드 연결 실패

### 증상
- 클라이언트 "서버에 연결할 수 없습니다" 안내

### 진단
1. `curl http://localhost:8000/health` (api healthy?)
2. CORS: `CORS_ALLOWED_ORIGINS`에 `app://insight` 포함?
3. JWT 만료: 앱 콘솔 (electron `Ctrl+Shift+I`)에서 401 확인 → refresh 흐름 점검
4. safeStorage 손상: 키체인에서 `insight-tokens` 삭제 후 재로그인

---

## 6. 사용자 동의 철회 후 재로그인

### 증상
- 사용자가 동의 철회 → 다른 기기에서 로그인 → 추천이 안 보임

### 정상 동작
- FR-59에 따라 추천 대시보드와 개인화 기능은 중단된 상태가 정확
- 클라이언트는 UI-05의 재동의/계정삭제 화면만 표시

### 흔한 오해
- 사용자가 "왜 추천이 안 떠요"라고 보고 → 동의 상태부터 확인. `GET /consent` `active=false`이면 정상 동작.

---

## 7. 토픽 연결 오류 누적

### 증상
- `topic_linkage_error` 테이블 행 누적 (FR-64)
- 관리자 콘솔에서 빨강 배지

### 진단
- 빈도가 가장 높은 `error_message` 패턴 확인
- 흔한 케이스: LLM 응답이 JSON parse 실패 / `cso_topic_ids`가 빈 배열

### 대응
- 단건 재처리: `POST /admin/topic-linkage/errors/{error_id}/retry`
- 일괄 재처리: 위 RPC를 batch로 호출
- 만성적 오류면 LLM 프롬프트 수정 (`algorithms/leaf-topic-lifecycle.md` 참조)

---

## 8. 시연 직전 깨끗한 데이터로 시작

> **🐧 모든 명령은 WSL 안에서 실행** (C-47, 2026-05-24). 본 문서 상단 §실행 환경 참조.
>
> **시연·개발 모드 = `make dev`** (C-50, 2026-05-24): docker-compose.dev.yml 자동 적용 → backend mount + uvicorn --reload → 코드 수정 시 0.5s 안 자동 reload (image rebuild 불필요). production 모드는 `make prod-up` (mount X, image 안 빌드된 코드만 사용).

```bash
docker compose down -v       # 모든 데이터 삭제
docker compose up -d postgres redis
make migrate
# (권장, C-46, 2026-05-24) git-tracked data/cso/CSO.3.5.csv 를 컨테이너 cso_cache volume 에 카피.
# KMI 서버 다운로드 skip — 오프라인 시연 + 트래픽 절감. 본 단계 생략 시 import-cso 가 자동 다운로드 fallback.
make seed-cso-cache             # FILE 생략 시 git-tracked data/cso/CSO.3.5.csv 자동 사용
make import-cso              # CSO 임포트
make create-admin            # admin 1
# make seed                  # A12 ⬜ 미구현 — Makefile 타깃 없음, backend/scripts/seed_personas.py 도 부재.
                             # 1차 시연 (~A6 단계) 은 수동 데이터 삽입 또는 signup → onboarding → /events 호출로 대체.
                             # A12 머지 후 본 명령 활성 예정 — 5+ 페르소나 + 14일 인터랙션.
make dev                     # api/worker/admin-console 기동 + dev mount (backend 코드 수정 즉시 반영)
cd client && npm start       # Electron 앱 (A9 🟡 진행 중 — `client/` 구축됨, main 다수 보강 머지. 시연 통과 검수 보류)
```

### 8.0 새 환경 / 다른 PC 재현 (C-50, 2026-05-24)

PR #33~#35 머지 후 main 코드 기준 — 추가 hot patch 불필요. `.env.example` 의 4 변수 (`COLD_START_MAX_PER_USER_LIFETIME=50`, `LLM_REQUEST_TIMEOUT_SECONDS=600`, `COLD_START_LLM_TIMEOUT_SECONDS=600`, `CORS_ALLOWED_ORIGINS` 에 127.0.0.1:5173 포함) 가 모두 영구화됨.

```bash
git clone <repo> && cd SKKU-InSight
cp .env.example .env                # 4 변수 default 그대로 사용 (수정 불필요)
make codex-login                    # ChatGPT OAuth (브라우저)
make dev                            # 첫 build ~5분 + 부트
make migrate && make seed-cso-cache && make import-cso && make create-admin
cd client && npm install && npm start   # Electron client
```

### 8.1 같은 환경 reuse (가장 빠른 재시연)

```bash
make stop                    # 컨테이너 stop (volume 보존)
# (다음 시연) wsl docker compose --project-name <YOUR_PROJECT> start
```
사용자 데이터 + rec + cso_topic 모두 그대로 (postgres + cso_cache volume 영속).

소요: 약 5–10분 (CSO 임포트가 가장 길다 — 1차 다운로드 1분 + insert ~3분).

### 8.1 `--reset` 운영 가드 (C-43, P2-16 + P2-10, 2026-05-19)

`make import-cso ARGS=--reset` 가 dynamic_leaf_topic 또는 user_cso_traversal 행이 존재하면 default 거부 (RuntimeError + 카운트 메시지). leaf 의 `cso_topic_ids` 가 orphan 되고 `user_cso_traversal.path` UUID 가 stale 되는 것이 의도된 경우만 우회:

```bash
make import-cso ARGS="--reset --force-orphan-cso-refs"
```

빈 DB (시연 환경) 는 카운트 0 이라 가드 발동 X. 운영 단계 진입 후 leaf/trace 데이터 누적된 시점부터 본 가드가 의미.

---

## 9. 로그 수집과 추적

- 모든 API 응답에 `X-Request-Id` 헤더
- 로그는 `structlog` JSON
- 같은 `request_id`로 grep:
  ```bash
  docker compose logs api | jq 'select(.request_id == "abc-123")'
  ```
