# 기술 스택 (라이브러리 핀)

본 파일은 SKKU InSight가 사용하는 라이브러리·런타임 버전을 핀하고 결정 근거를 한 줄씩 기록한다. `pyproject.toml`, `package.json`, `Dockerfile` 작성 시 본 문서를 참고한다.

## 백엔드 (Python 3.12)

| 라이브러리 | 버전 핀 | 결정 근거 |
|---|---|---|
| `fastapi` | `^0.115` | OpenAPI 자동 생성 + Pydantic v2 통합 |
| `uvicorn[standard]` | `^0.30` | 비동기 ASGI 표준 |
| `pydantic` | `^2.7` | 입출력 스키마 검증 |
| `pydantic-settings` | `^2.3` | env-vars 모델화 |
| `sqlalchemy[asyncio]` | `^2.0` | ORM async 지원 |
| `alembic` | `^1.13` | 스키마 마이그레이션 표준 |
| `asyncpg` | `^0.29` | Postgres async 드라이버 |
| `bcrypt` | `>=4.1` | 비밀번호 해시 직접 호출 + SHA-256 hex pre-hash (C-11 fix, `passlib` 1.7.4 bcrypt 4.x 호환성 깨짐으로 폐기). UTF-8 한국어 128자 정책 보장 |
| `python-jose[cryptography]` | `^3.3` | JWT 발급/검증 (NFR-17) |
| `slowapi` | `^0.1.9` | Rate limiting (Redis 백엔드, NFR 보안) |
| `redis` | `^5.0` | Refresh token store, 추천 캐시, 분산 semaphore (C-19), Lua dwell cap (A6) |
| `rq` | `>=2.0` | 작업 큐 (Redis 기반, 별도 인프라 불필요) |
| `rq-scheduler` | `^0.13` | cron job 등록 |
| `httpx` | `^0.27` | async HTTP 클라이언트 (LLM provider, clickbait service) |
| `networkx` | `^3.3` | CSO 그래프 인접/거리 탐색 메모리 캐시 |
| `rapidfuzz` | `>=3.6,<4` | 제목 유사도 dedup (`python-Levenshtein` 폐기, A4 round 2 후속) |
| `tomli` / `tomllib` | stdlib (3.11+) | `interest_params.toml` 등 로딩 |
| `structlog` | `^24.1` | 구조화 로그 |

> **v13 라운드 (2026-05-11) 폐기**: `beautifulsoup4` / `lxml` / `feedparser` (6 source 어댑터 폐기 — NaverBS4/RSS 미사용) / `tenacity` (외부 API retry 대상 사라짐 — LLM provider 자체 retry). `passlib[bcrypt]` 는 A2 자체 검수 단계에서 bcrypt 4.x 호환성 문제로 폐기 (C-11). 모두 `pyproject.toml` 에 미포함.

### 백엔드 도구

| 도구 | 버전 | 근거 |
|---|---|---|
| `pytest` | `^8.2` | 단위·통합 테스트 |
| `pytest-asyncio` | `^0.23` | 비동기 테스트 |
| `pytest-cov` | `^5.0` | 커버리지 |
| `ruff` | `^0.5` | Lint + Format (black/isort/flake8 대체) |
| `mypy` | `^1.10` | 정적 타입 검사 |

> **v13 라운드 폐기**: `vcrpy` (외부 HTTP 호출 녹화) — A4 round 2 부터 `LLMProvider=mock` deterministic fixture (`tests/collection/fixtures/llm_search_mock/`) 가 그 역할 대체. dev 의존성 미포함.

## 클라이언트 (Electron + React)

| 라이브러리 | 버전 핀 | 결정 근거 |
|---|---|---|
| `electron` | `^31` | Windows 데스크톱 셸 |
| `electron-builder` | `^24` | Windows installer 빌드 |
| `react` / `react-dom` | `^18.3` | 컴포넌트 표준 |
| `typescript` | `^5.5` | 타입 안전성 |
| `vite` | `^5.3` | 빠른 dev server (Electron preload 통합) |
| `@vitejs/plugin-react` | `^4.3` | Vite + React |
| `react-router-dom` | `^6.24` | UI-01~05 라우팅 |
| `zustand` | `^4.5` | 가벼운 클라이언트 상태 (Redux 대체) |
| `@tanstack/react-query` | `^5.51` | API 캐시·재시도 |
| `react-i18next` | `^14.1` | 한국어 i18n (콘텐츠 한·영 병행) |
| `i18next` | `^23.12` | i18next 코어 |
| `axios` | `^1.7` | HTTP 클라이언트 |
| `electron safeStorage` (electron 내장) | n/a | OS 키체인 토큰 보관 |

### 클라이언트 도구

| 도구 | 버전 | 근거 |
|---|---|---|
| `vitest` | `^2.0` | React 컴포넌트 테스트 |
| `@testing-library/react` | `^16.0` | UI 테스트 |
| `eslint` | `^9` | Lint |
| `prettier` | `^3.3` | Format |

## 관리자 콘솔 (Next.js)

| 라이브러리 | 버전 핀 | 결정 근거 |
|---|---|---|
| `next` | `^14.2` | App Router + Server Components |
| `react` / `react-dom` | `^18.3` | 동일 |
| `typescript` | `^5.5` | 동일 |
| `@tanstack/react-query` | `^5.51` | 동일 |
| `tailwindcss` | `^3.4` | 빠른 운영 UI |
| `recharts` | `^2.12` | 낚시성 통계 차트 |
| `react-i18next` | `^14.1` | 한국어 |

## 인프라

| 항목 | 버전 | 근거 |
|---|---|---|
| `postgres` | `16-alpine` | JSONB + 안정성 |
| `redis` | `7-alpine` | 큐·캐시·rate limit |
| Docker Engine | `>=24` | compose v2 |
| Docker Compose | `>=2.27` | profiles, healthcheck |

## CI

| 항목 | 버전 | 근거 |
|---|---|---|
| GitHub Actions | runner `ubuntu-latest` | 무료 분량 충분 |
| `actions/setup-python` | `v5` | Python 3.12 setup |
| `actions/setup-node` | `v4` | Node 20 setup |
