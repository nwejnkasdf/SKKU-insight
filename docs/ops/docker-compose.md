# docker-compose 구성

본 파일은 `docker-compose.yml`의 서비스 정의 골격이다. 환경변수는 [`env-vars.md`](env-vars.md), 배포 절차는 [`../sdd/deployment.md`](../sdd/deployment.md), 관리자 부트스트랩은 [`admin-bootstrap.md`](admin-bootstrap.md).

## 파일 위치

- `docker-compose.yml` (프로젝트 루트)
- `.env.example` (프로젝트 루트, [`env-vars.md`](env-vars.md) 카탈로그 미리 채워둠)
- `backend/Dockerfile` — api + worker 공유 이미지
- `admin-console/Dockerfile`
- `clickbait_module/Dockerfile` (옵션, 자체 호스팅 시) — clickbait 모듈은 호스팅·transport가 운영 결정. [`../algorithms/clickbait-integration.md`](../algorithms/clickbait-integration.md) §호스팅·transport 추상화 참조

## 서비스 정의 (A2 실제 산출 — `docker-compose.yml`)

A2 가 만든 실제 `docker-compose.yml` 은 본 문서의 골격을 따르되 다음과 같이 단순화:
- `version` 필드 제거 (compose v2+ 에서 deprecated)
- `x-backend-base` anchor 미사용 (개별 service 명시)
- api/worker 의 `volumes: ./backend:/app:ro` 제거 — Dockerfile 이 `COPY . /app` 으로 빌드 시점에 포함
- api `command` 가 `--workers ${UVICORN_WORKERS:-1}` 사용 (멀티 워커 정책, decision-backlog C-20)
- worker `command` 가 `python -m app.worker` — RQ 2.x 의 `Connection` context manager 제거 + `worker/__init__.py` 가 jobs 패키지 사전 import 로 unpickle 보장
- admin-console 은 1차 placeholder (Next.js stub — A10 가 본격 구현)
- clickbait-detector 는 default 미포함 (`CLICKBAIT_SERVICE_URL` env 로 외부 호스팅 우선)

```yaml
# docker-compose.yml (A2 실제 산출)
services:
  postgres:
    image: postgres:16-alpine
    env_file: .env
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    # v13 round 3 R3-C03 fix (2026-05-16): 호스트 포트 5433 — native PostgreSQL (5432) 충돌 회피.
    # 컨테이너 내부는 5432 그대로. DATABASE_URL 의 host 가 컨테이너 이름이라면 5432 유지.
    ports:
      - "127.0.0.1:5433:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  api:
    build: { context: ./backend, dockerfile: Dockerfile }
    restart: unless-stopped
    env_file: [.env]
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_URL: ${REDIS_URL}
      UVICORN_WORKERS: ${UVICORN_WORKERS:-1}
    command: ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1}"]
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 3s
      retries: 5

  worker:
    build: { context: ./backend, dockerfile: Dockerfile }
    restart: unless-stopped
    env_file: [.env]
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_URL: ${REDIS_URL}
      REDIS_URL_QUEUE: ${REDIS_URL_QUEUE}
    command: ["python", "-m", "app.worker"]
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      api: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "rq info --url $${REDIS_URL_QUEUE} | grep -q '0 workers, 0 queues' && exit 1 || exit 0"]
      interval: 30s
      timeout: 5s
      retries: 3

  # clickbait-detector — 호스팅·transport는 운영 결정 (default: docker-compose에 정의하지 않음).
  # 자체 호스팅으로 도커 컴포즈에 포함하려면 아래 블록 주석 해제 + clickbait_module/Dockerfile 준비
  # + backend env CLICKBAIT_SERVICE_URL=http://clickbait-detector:8100 설정.
  # 외부 호스팅(별도 GPU 머신, 클라우드 등) 시 본 블록 그대로 두고 backend env CLICKBAIT_SERVICE_URL을 외부 URL로 지정.
  # clickbait-detector:
  #   build:
  #     context: ./clickbait_module
  #     dockerfile: Dockerfile
  #   env_file: .env
  #   ports:
  #     - "127.0.0.1:8100:8100"
  #   volumes:
  #     - ./models:/models:ro
  #   healthcheck:
  #     test: ["CMD-SHELL", "curl -f http://localhost:8100/health || exit 1"]
  #     interval: 15s
  #     timeout: 3s
  #     retries: 3
  #   restart: unless-stopped

  admin-console:
    # 1차 placeholder (A10 본격 구현 시 build context 교체).
    image: node:20-alpine
    restart: unless-stopped
    working_dir: /app
    command: ["sh", "-c", "echo 'admin-console placeholder' && tail -f /dev/null"]
    environment:
      NEXT_PUBLIC_API_BASE: ${API_PUBLIC_BASE}
    ports:
      - "127.0.0.1:3001:3000"
    depends_on:
      api: { condition: service_healthy }

volumes:
  pg_data: {}
  redis_data: {}
```

## .env 매핑

`.env`는 `.env.example`에서 복사 (값은 [`env-vars.md`](env-vars.md) 참조). 주요 매핑:

| docker-compose 참조 | env-var |
|---|---|
| `${POSTGRES_DB}` | `POSTGRES_DB=insight` |
| `${POSTGRES_USER}` | `POSTGRES_USER=insight` |
| `${POSTGRES_PASSWORD}` | `POSTGRES_PASSWORD=...` (lifespan 이 placeholder 차단, C-22) |
| `${DATABASE_URL}` | `postgresql+asyncpg://...` (api/worker) |
| `${REDIS_URL}` | `redis://redis:6379/0` (default DB) |
| `${REDIS_URL_QUEUE}` (worker) | `redis://redis:6379/2` (RQ 큐) |
| `${UVICORN_WORKERS}` | default `1`. 멀티 워커 시 LLM 동시성은 Redis 분산 (C-19), DB pool 합산은 운영자 책임 (C-20) |
| `${LLM_PROVIDER}` | api/worker 가 사용. mock(default) / openai / anthropic / openrouter / codex_oauth |
| `${ADMIN_BOOTSTRAP_*}` | api 첫 부팅 admin 생성. password 는 정책 통과해야 (C-22) |

## profiles (선택)

개발 시 일부 서비스만 띄우려면 compose profiles 활용 가능. 1차 결정: profiles 미사용. 항상 5개(postgres / redis / api / worker / admin-console) 부팅. clickbait-detector는 자체 호스팅 시에만 추가. demo 모드는 `--build`만 다름.

## 명령

```bash
# 1회 setup
cp .env.example .env
# JWT_SECRET (64+ random), POSTGRES_PASSWORD, ADMIN_BOOTSTRAP_PASSWORD 채우기
# placeholder 값은 lifespan validator 가 부팅 시 차단 (C-22)
docker compose build
docker compose up -d postgres redis
make migrate         # alembic upgrade head (8 테이블 + sentinel + SourcePolicy 3행)
make create-admin    # AdminUser 1행 (must_change_password=true)
make import-cso      # (A3 머지 후) CSO 임포트 + BroadInterest 12 시드

# 매일 부트
docker compose up -d
docker compose logs -f api worker

# A2 가 제공하는 검증 보조
make test            # docker compose exec api pytest tests -v
make lint            # ruff + mypy --strict
make check-all       # 6 cross-check (api_docs / schema / env / error_codes / redis_keys / contracts)
```

## 종료/리셋

```bash
docker compose down              # 컨테이너만 중지 (볼륨 보존)
docker compose down -v           # 데이터까지 삭제 (재시연 전 깨끗한 상태)
```

`down -v`는 NFR-21에 영향 없음 — 운영 시연 환경에서만 사용.
