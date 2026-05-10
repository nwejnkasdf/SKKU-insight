# A2 — Backend Foundation 본문 (Phase 0b)

> 본 prompt를 새 세션의 첫 메시지로 그대로 붙여 넣는다. 작업 디렉토리는 ``.
> **사전조건**: Phase 0a A2-stub 완료 + commit (contracts.py + endpoint stub + OpenAPI export 동작).

## 너의 역할

Phase 0a stub의 본문을 채운다. **인증·동의·온보딩·사용자 모듈**을 진짜 동작하도록 구현 + Alembic migration + docker-compose 부트.

다른 모듈(topic / interest / collection / recommendation / admin)은 본 세션에서 손대지 말 것 (다른 에이전트가 담당).

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` 의 "첫 5분" 5개 + 다음:

- `docs/sdd/architecture.md`
- `docs/sdd/module-boundaries.md` (`app/auth`, `app/consent`, `app/user`, `app/onboarding`, `app/security`, `app/db` 부분)
- `docs/sdd/concurrency.md` (§1 DB pool, §3 user-level mutex, §4.2 active_day atomic, §7 consent cache)
- `docs/data/schema.md` (User, AdminUser, UserConsent, UserCSOTraversal, sentinel Source 시드)
- `docs/api/auth.md`, `consent.md`, `onboarding.md`
- `docs/security/auth-flow.md`, `token-handling.md`, `password-policy.md`, `rate-limiting.md`
- `docs/ops/docker-compose.md`, `env-vars.md`, `admin-bootstrap.md`
- `docs/algorithms/cold-start.md` (onboarding이 호출하는 부분)

## 산출

### 1. Alembic + DB 모델
- `backend/alembic/` 초기화 + 첫 migration: User, AdminUser, UserConsent, UserCSOTraversal, **BroadInterest(테이블만 — 시드는 A3 책임. `cso_seed_topic_id` FK 가 `cso_topic.cso_topic_id` 의존이므로 CSO 임포트 후 시드 필요)**, CSOTopic(빈 테이블 — A3 가 시드), Source(+ sentinel `cold_start_pseudo` 시드), SourcePolicy(3 시드)
- **단** A3가 CSO 임포트 + BroadInterest 12 시드 담당. 본 세션은 빈 CSOTopic + 빈 BroadInterest 테이블 + Source sentinel만.
- nullable composite PK 룰 (DocumentTopic, NotInterestedTopic, UserInterestState UNIQUE)은 본 세션 범위 외 — A3·A6·A8이 담당. 단 partial unique index 패턴은 schema.md 예시 그대로 따름.
- active_day_counter, last_active_calendar_date 컬럼 포함 (User).

### 2. auth / consent / user 모듈 본문
- bcrypt cost=12 (passlib)
- JWT Access 15m + Refresh opaque + Redis store + rotation + replay detect (`docs/security/token-handling.md`)
- slowapi rate limit (rate-limiting.md 정책)
- `/auth/signup`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/me`
- `/consent`, `/consent/revoke`, `/consent/account-deletion`
- consent middleware (Redis 60s cache, concurrency.md §7)
- 사용자 계정 삭제는 1차 즉시 cascade (NFR-21 grace 미해소, decision-backlog C-2)

### 3. onboarding 모듈 본문
- `GET /topics/cso/clusters` 는 A3가 담당하므로 본 세션은 stub `raise NotImplementedError` 유지
- `POST /onboarding/interests` — single-flight Redis lock (concurrency.md §2). RQ enqueue → cold-start LLM 호출.
  - 단 LLM 호출 자체는 `MockProvider` 또는 `LLMProvider` 추상 호출. 실제 cold-start orchestrator 구현은 A8가 담당. 본 세션은 RQ enqueue까지만.
- `GET /onboarding/cold-start-status/{request_id}` — Redis에서 status 조회
- `PUT /onboarding/interests` (FR-55, 설정에서 관심 분야 수정) — prior boost 갱신만, trace stale 마킹은 A7이 처리

### 4. 보안 미들웨어
- JWT 검증 미들웨어 (`aud` 강제, denylist 체크)
- consent active 검증 미들웨어 (personalization endpoint 적용)
- structlog mask (password/token)
- HTTPS·CORS (env-vars.md)
- Idempotency-Key 헤더 처리 (concurrency.md §3)

### 5. docker-compose.yml
- postgres / redis / api / worker / admin-console(stub) 5 서비스 default. clickbait-detector는 옵션(자체 도커 호스팅 시에만; default는 외부 호스팅으로 backend env `CLICKBAIT_SERVICE_URL`이 외부 URL 가리킴)
- `docs/ops/docker-compose.md` 골격 그대로
- DB pool 분리 (api 30 / worker 10) — config.py 에서

### 6. Makefile
- `migrate`, `create-admin`, `import-cso`(A3가 implement), `seed`(A12가 implement), `dev`, `demo`

### 7. LLMProvider 추상 + MockProvider
- `backend/app/llm_provider/` 디렉토리
- `LLMProvider` Protocol (module-boundaries.md)
- `MockProvider` 구현체: prompt hash → `tests/fixtures/mock_llm/{hash}.json` 매핑. 본 세션에서는 fixture 디렉토리만 만들어 두고, 실제 fixture는 A12 또는 A8 cold-start 작업 시 채움.
- `LLM_PROVIDER` env 토글 (mock/openai/anthropic/openrouter/codex_oauth — 1차는 mock + openai만 동작, 나머지는 stub)

## 헌법 (재강조)

- **contracts.py 외 enum 정의 금지**. 모든 enum import만.
- **다른 모듈 시그니처 추측 금지**. topic/interest/collection/recommendation/admin endpoint는 stub 유지.
- **DB schema 변경 시 alembic + docs/data/schema.md 동시 수정**. schema.md는 기존 명세 그대로이므로 alembic만 작성하면 됨.
- **새 환경변수 시 BaseSettings + docs/ops/env-vars.md + .env.example 동시**.
- **자기 모듈 외 파일 수정 시 PR description에 명시**.

## 검증

```bash
cd backend
docker compose up -d postgres redis
make migrate          # alembic upgrade head, 모든 테이블 생성
make create-admin     # AdminUser 1행 (env에서 읽음)
docker compose up -d api worker
curl http://localhost:8000/health        # 200
curl -X POST http://localhost:8000/auth/signup -d '{"email":"test@test.com","password":"TestPassword2026!"}' -H "Content-Type: application/json"
# 201 + user_id

mypy --strict backend/app/
ruff check backend/
pytest backend/tests/auth backend/tests/consent backend/tests/onboarding -v
python scripts/check_api_docs.py  # auth/consent/onboarding 부분 통과
python scripts/check_schema.py
python scripts/check_env.py
python scripts/check_contracts.py
python scripts/export_openapi.py > openapi.json   # diff 없어야
```

테스트는 최소:
- signup·login·refresh·logout 각 1개씩
- consent 등록·철회·account-deletion 각 1개씩
- onboarding/interests POST 1개 + cold-start-status polling 1개
- consent 비활성 시 403 검증

## 출력 형식

`_common-disambiguation.md` "출력 형식" 그대로. 추가:

- 신규 endpoint 본문 갯수
- Alembic migration 갯수
- 시드 데이터 (BroadInterest 12, SourcePolicy 3, Source sentinel 1, AdminUser 1) 확인
- docker compose 부트 시간

다음 Phase에 영향 줄 결정·미결 사항 보고.
