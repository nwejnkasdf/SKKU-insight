# docker-compose 구성

본 파일은 `docker-compose.yml`의 서비스 정의 골격이다. 환경변수는 [`env-vars.md`](env-vars.md), 배포 절차는 [`../sdd/deployment.md`](../sdd/deployment.md), 관리자 부트스트랩은 [`admin-bootstrap.md`](admin-bootstrap.md).

## 파일 위치

- `docker-compose.yml` (프로젝트 루트)
- `.env.example` (프로젝트 루트, [`env-vars.md`](env-vars.md) 카탈로그 미리 채워둠)
- `backend/Dockerfile` — api + worker 공유 이미지
- `admin-console/Dockerfile`
- `clickbait_module/Dockerfile` (옵션, 자체 호스팅 시) — clickbait 모듈은 호스팅·transport가 운영 결정. [`../algorithms/clickbait-integration.md`](../algorithms/clickbait-integration.md) §호스팅·transport 추상화 참조

## 서비스 정의 골격

```yaml
# docker-compose.yml
version: "3.9"

x-backend-base: &backend-base
  build:
    context: ./backend
    dockerfile: Dockerfile
  env_file: .env
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy
  volumes:
    - ./backend:/app:ro
  restart: unless-stopped

services:
  postgres:
    image: postgres:16-alpine
    env_file: .env
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "127.0.0.1:5432:5432"
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
    <<: *backend-base
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload   # demo 모드는 --reload 제거
    ports:
      - "127.0.0.1:8000:8000"
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
      interval: 10s
      timeout: 3s
      retries: 5

  worker:
    <<: *backend-base
    command: rq worker --with-scheduler default leaf_lifecycle merge_evaluation summary_generation
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      api:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "rq info --url $${REDIS_URL} || exit 1"]
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
    build:
      context: ./admin-console
      dockerfile: Dockerfile
    env_file: .env
    environment:
      NEXT_PUBLIC_API_BASE: http://localhost:8000
    ports:
      - "127.0.0.1:3001:3000"
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped

volumes:
  pg_data:
  redis_data:
```

## .env 매핑

`.env`는 `.env.example`에서 복사 (값은 [`env-vars.md`](env-vars.md) 참조). 주요 매핑:

| docker-compose 참조 | env-var |
|---|---|
| `${POSTGRES_DB}` | `POSTGRES_DB=insight` |
| `${POSTGRES_USER}` | `POSTGRES_USER=insight` |
| `${POSTGRES_PASSWORD}` | `POSTGRES_PASSWORD=...` |
| `${REDIS_URL}` (worker) | `REDIS_URL=redis://redis:6379/0` |
| `${LLM_PROVIDER}` | api/worker가 사용 |
| `${ADMIN_BOOTSTRAP_*}` | api 첫 부팅 admin 생성 |

## profiles (선택)

개발 시 일부 서비스만 띄우려면 compose profiles 활용 가능. 1차 결정: profiles 미사용. 항상 5개(postgres / redis / api / worker / admin-console) 부팅. clickbait-detector는 자체 호스팅 시에만 추가. demo 모드는 `--build`만 다름.

## 명령

```bash
# 1회 setup
cp .env.example .env
docker compose build
docker compose up -d postgres redis
make migrate         # alembic upgrade head
make import-cso      # CSO 임포트 (1회)
make create-admin    # AdminUser 1행 생성

# 매일 부트
docker compose up -d
docker compose logs -f api worker
```

## 종료/리셋

```bash
docker compose down              # 컨테이너만 중지 (볼륨 보존)
docker compose down -v           # 데이터까지 삭제 (재시연 전 깨끗한 상태)
```

`down -v`는 NFR-21에 영향 없음 — 운영 시연 환경에서만 사용.
