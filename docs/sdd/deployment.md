# 배포 — Docker Compose

본 파일은 SKKU InSight 로컬 풀스택 데모 배포 구성을 정의한다. 시연·개발 모두 단일 `docker-compose.yml`로 기동하며, Electron 클라이언트만 호스트에서 `npm start`. 환경변수는 [`../ops/env-vars.md`](../ops/env-vars.md), CI/CD는 [`../ops/ci-cd.md`](../ops/ci-cd.md) 참고.

## 서비스 목록

| 서비스 | 이미지 / Dockerfile | 포트 (호스트:컨테이너) | 의존 | 볼륨 | Healthcheck |
|---|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | `5432:5432` | (없음) | `pg_data:/var/lib/postgresql/data` | `pg_isready -U insight` |
| `redis` | `redis:7-alpine` | `6379:6379` | (없음) | `redis_data:/data` | `redis-cli ping` |
| `api` | `./backend/Dockerfile` (uvicorn) | `8000:8000` | `postgres`, `redis` | `./backend:/app:ro` (개발 모드 hot reload) | `curl -f http://localhost:8000/health` |
| `worker` | `./backend/Dockerfile` (rq worker entrypoint) | (없음) | `postgres`, `redis` | `./backend:/app:ro` | `rq info` |

> **DB pool 분리** ([`../sdd/concurrency.md §1`](concurrency.md)): api와 worker가 같은 `DATABASE_URL`을 쓰지만 application 레벨에서 풀을 분리한다. api는 `PG_API_POOL_MAX=30` (사용자 20명 + 폴링 + 캐시 갱신 여유), worker는 `PG_WORKER_POOL_MAX=10` (수집·라이프사이클·병합 잡). 같은 풀을 공유하면 worker의 긴 작업이 api 요청을 굶긴다.
| `clickbait-detector` | `./services/clickbait-detector/Dockerfile` (옵션, 자체 도커 호스팅 시) | `8100:8100` | (없음, 모델 in-process) | `./models:/models:ro` | `curl -f http://localhost:8100/health` |
| `admin-console` | `./admin-console/Dockerfile` (Next.js) | `3001:3000` | `api` | `./admin-console:/app:ro` | `curl -f http://localhost:3000` |

> `clickbait-detector`는 호스팅·transport가 운영 결정. 외부 호스팅 시 본 컨테이너는 정의하지 않고 backend env `CLICKBAIT_SERVICE_URL`이 외부 URL을 가리킨다. 자세히는 [`../algorithms/clickbait-integration.md`](../algorithms/clickbait-integration.md) §호스팅·transport 추상화.

Electron 클라이언트(`./client`)는 컨테이너에 포함하지 않는다. 호스트에서 `npm install && npm start`로 띄우고 `.env.local`의 `VITE_API_BASE=http://localhost:8000`을 통해 `api` 서비스에 붙는다.

## 의존 그래프

```
electron-app (host)
   └──> api ─┬──> postgres
             ├──> redis ─── worker
             ├──> clickbait-detector
             └──> llm-adapter (in-process import)

admin-console ──> api
worker ──┬──> postgres
         ├──> redis
         ├──> clickbait-detector
         └──> external sources (arXiv/OpenAlex/...)
```

## 개발 모드 vs 시연 모드

| 항목 | 개발 모드 (`make dev`) | 시연 모드 (`make demo`) |
|---|---|---|
| api hot reload | uvicorn `--reload` | 끔 |
| worker autoreload | rq watcher | 끔 |
| 시드 데이터 | 부트 시 `python scripts/seed_personas.py --no-events` | 부트 시 14일치 인터랙션 포함 (`scripts/seed_personas.py --full`) |
| LLM_PROVIDER | `mock` (default — CI/단위 테스트/시연 fixture) / `openai` / `anthropic` / `openrouter` / `codex_oauth` (local experimental, 본인 토이 빌드 전용) | `mock` |
| 수집 cron | 사용자 수동 트리거 `POST /collection/jobs/me/run-now` (1/시간/사용자); 관리자 재실행 `POST /admin/collection/jobs/{id}/reprocess` | demo 모드는 별도 `COLLECTION_CRON_DEMO=0 * * * *` (env-vars.md) |
| Postgres logs | `log_statement=all` | `log_statement=ddl` |
| 외부 소스 호출 | recorded fixture (vcrpy) | live network |

## 볼륨 정의

```
volumes:
  pg_data:       # Postgres 영속 (시연 후 docker compose down -v로만 삭제)
  redis_data:    # Redis 영속 (작업 큐, refresh token 메타)
  cso_cache:     # CSO N3 다운로드 후 임포트 결과 caching (재기동 가속)
```

## 네트워크

기본 bridge 네트워크 1개. 외부에 노출할 포트는 8000 (api), 3001 (admin-console), 8100 (clickbait, debug 용도), 5432/6379 (개발자 직접 접근). 시연 환경에서 5432/6379는 `127.0.0.1`에만 바인딩.

## 동시성·부하 가정

**10-20명 동시 사용자**를 1차 운영 가정으로 한다 (시연 + 클래스 평가). 이 부하 하의 정합성·NFR-12 보장을 위한 모든 동시성 가드 패턴은 [`concurrency.md`](concurrency.md)에 정리되어 있다. 핵심:

- DB pool: api 30 / worker 10 분리
- Recommendation 캐시: single-flight (사용자당 in-flight build 1개)
- Trace mutation: user-level Redis mutex (사용자당 1개 직렬화)
- 베이지안·active_day_counter: atomic SQL UPDATE
- LLM 호출: 전역 8 + 사용자당 2 동시 cap
- Event batch: dwell_tick 5초 윈도우 batch flush
- Consent active: Redis 60초 cache
- 일일 수집: user_id 해시 기반 5분 jitter

`docker-compose.yml`은 위 가정을 위해 Postgres `max_connections >= 50`을 보장 (default 100이라 여유). Redis는 `maxmemory-policy allkeys-lru` 명시.

## 시연 부트 절차

1. `cp .env.example .env` 후 필요한 비밀값 채움. 1차 부트는 `LLM_PROVIDER=mock`이 default라 별도 키 없이 동작. 실제 LLM 호출이 필요한 시연이면 `LLM_PROVIDER=openai` + `OPENAI_API_KEY` 또는 본인 토이 빌드 한정 `LLM_PROVIDER=codex_oauth` + `CODEX_OAUTH_TOKEN` (`../ops/env-vars.md`).
2. `make import-cso` (1회) — CSO N3 다운로드 → Postgres 시드 → NetworkX 캐시 생성.
3. `make create-admin` (1회) — 관리자 계정 생성 (`../ops/admin-bootstrap.md`).
4. `docker compose up --build -d` — 6 서비스 기동.
5. `make seed` — 5+ 페르소나 + 14일 인터랙션 (`../data/seed-personas.md`).
6. `cd client && npm install && npm start` — Electron 앱 실행.
