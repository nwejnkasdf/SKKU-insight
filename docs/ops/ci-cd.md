# CI/CD (GitHub Actions)

본 파일은 SKKU InSight의 GitHub Actions 워크플로 표를 정의한다. 1차 목표는 PR 머지 전 자동 검증과 빌드 가능성 확인. 자동 배포는 1차 시연 환경에서 하지 않는다 (로컬 docker compose 데모). 관련: [`../sdd/tech-stack.md`](../sdd/tech-stack.md), [`../srs/08-acceptance-tests.md`](../srs/08-acceptance-tests.md).

## 워크플로 표

| 워크플로 파일 | 트리거 | 잡 |
|---|---|---|
| `.github/workflows/contracts.yml` | push / PR | **check_api_docs, check_schema, check_env, check_error_codes, check_redis_keys, check_contracts, openapi_diff, codegen_diff** ([`../sdd/agent-orchestration.md §5`](../sdd/agent-orchestration.md)) |
| `.github/workflows/ci.yml` | push / PR | lint-py(ruff), type-py(mypy --strict), test-py, lint-ts, type-ts, test-ts |
| `.github/workflows/build.yml` | push to main + tags | build-api-image, build-clickbait-image, build-admin-console |
| `.github/workflows/electron-build.yml` | tag `v*` | win-installer, mac-installer (수동 dispatch만 처음에는) |

> **contracts.yml은 ci.yml보다 먼저 통과**해야 한다. 통신 규격 깨짐을 type 검사·테스트보다 앞서 차단. 자세한 룰은 [`../sdd/agent-orchestration.md`](../sdd/agent-orchestration.md), [`../sdd/contracts.md`](../sdd/contracts.md).

## 잡 정의 골격

### `ci.yml`

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  lint-py:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -e backend[dev]
      - run: ruff check backend
      - run: ruff format --check backend

  type-py:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e backend[dev]
      - run: mypy --strict backend/app

  contracts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: actions/setup-node@v4
      - run: pip install -e backend[dev]
      - run: python scripts/check_api_docs.py
      - run: python scripts/check_schema.py
      - run: python scripts/check_env.py
      - run: python scripts/check_error_codes.py
      - run: python scripts/check_redis_keys.py
      - run: python scripts/check_contracts.py
      - run: python scripts/export_openapi.py > openapi.json
      - run: git diff --exit-code openapi.json   # codegen 안 되면 fail
      - run: cd client && npm ci && npm run codegen
      - run: cd client && git diff --exit-code src/generated   # client codegen
      - run: cd admin-console && npm ci && npm run codegen
      - run: cd admin-console && git diff --exit-code src/generated

  test-py:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: insight_test
          POSTGRES_USER: insight
          POSTGRES_PASSWORD: insight
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 3s --health-retries 10
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
        options: --health-cmd "redis-cli ping" --health-interval 5s --health-timeout 3s --health-retries 10
    env:
      DATABASE_URL: postgresql+asyncpg://insight:insight@localhost:5432/insight_test
      REDIS_URL: redis://localhost:6379/0
      LLM_PROVIDER: mock
      JWT_SECRET: ${{ secrets.JWT_SECRET_TEST }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e backend[dev]
      - run: alembic upgrade head
        working-directory: backend
      - run: pytest --cov=app --cov-report=xml
        working-directory: backend

  lint-ts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
      - run: npm ci
        working-directory: client
      - run: npm run lint
        working-directory: client
      - run: npm ci
        working-directory: admin-console
      - run: npm run lint
        working-directory: admin-console

  type-ts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: npm }
      - run: npm ci
        working-directory: client
      - run: npm run typecheck
        working-directory: client
      - run: npm ci
        working-directory: admin-console
      - run: npm run typecheck
        working-directory: admin-console

  test-ts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: npm }
      - run: npm ci
        working-directory: client
      - run: npm run test
        working-directory: client
      - run: npm ci
        working-directory: admin-console
      - run: npm run test
        working-directory: admin-console
```

### `build.yml`

```yaml
name: Build images
on:
  push:
    branches: [main]
    tags: ["v*"]

jobs:
  build-api-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - run: docker build -t insight-api:${{ github.sha }} ./backend

  build-clickbait-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t insight-clickbait:${{ github.sha }} ./services/clickbait-detector

  build-admin-console:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t insight-admin:${{ github.sha }} ./admin-console
```

### `electron-build.yml` (수동 dispatch + tag)

```yaml
name: Electron build
on:
  workflow_dispatch:
  push:
    tags: ["v*"]

jobs:
  win-installer:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm ci
        working-directory: client
      - run: npm run dist:win
        working-directory: client
      - uses: actions/upload-artifact@v4
        with:
          name: insight-win-installer
          path: client/release/*.exe
```

mac-installer는 EV-01 단계에서 추가. 1차 시연은 Windows만.

## 시크릿

| 시크릿 | 용도 |
|---|---|
| `JWT_SECRET_TEST` | CI 테스트 환경 JWT 서명 |
| `OPENAI_API_KEY` (선택) | LLM 통합 테스트 시 |
| `CODEX_OAUTH_TOKEN` (선택) | 동일 |

기본은 `LLM_PROVIDER=mock`로 LLM 의존을 끊은 단위 테스트만 실행.

## 검증되는 AT

- 자동: AT-01, AT-02, AT-03, AT-04, AT-07, AT-08, AT-09, AT-11, AT-12, AT-13, AT-15 ([`../srs/08-acceptance-tests.md`](../srs/08-acceptance-tests.md))
- 수동: AT-05, AT-06, AT-10, AT-14 — 시연 체크리스트로 관리 (`../sdd/deployment.md` 시연 모드)

## 머지 정책

| 잡 | required for merge |
|---|---|
| lint-py, type-py, test-py | yes |
| lint-ts, type-ts, test-ts | yes |
| build-* | no (실패는 즉시 보고하지만 머지 차단 X) |
