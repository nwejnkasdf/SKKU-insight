# SKKU InSight — A2 Phase 0b Makefile.
# 모든 타깃은 repo root 에서 실행. docker compose 실서비스 가정.

.PHONY: help dev demo migrate create-admin import-cso reset-password test lint check-all ci-regression-net stop down clean codex-login codex-status

help:
	@echo "주요 타깃:"
	@echo "  make dev            - docker compose up -d (postgres, redis, api, worker, admin-console)"
	@echo "  make demo           - dev + 데모용 cron 더 자주 (COLLECTION_CRON=COLLECTION_CRON_DEMO)"
	@echo "  make migrate        - alembic upgrade head (api 컨테이너 안에서)"
	@echo "  make create-admin   - AdminUser 부트스트랩 INSERT"
	@echo "  make import-cso     - CSO 3.4 임포트 (cso_topic ~14k + broad_interest 12) — A3"
	@echo "  make reset-password EMAIL=user@x NEW=newpw - 사용자 비번 강제 변경 (P2-5)"
	@echo "  make test           - pytest backend/tests (docker compose 실서비스 사용)"
	@echo "  make lint           - ruff + mypy --strict"
	@echo "  make check-all      - 6 cross-check scripts + ruff + mypy"
	@echo "  make ci-regression-net - PASS-TO-PASS 회귀 그물 (pytest + ruff + mypy + 6 check + alembic upgrade)"
	@echo "  make stop           - docker compose stop (데이터 유지)"
	@echo "  make down           - docker compose down (네트워크 제거)"
	@echo "  make clean          - docker compose down -v (볼륨 삭제 — destructive)"
	@echo ""
	@echo "CodexOAuthProvider (LLM_PROVIDER=codex_oauth 시):"
	@echo "  make codex-login    - 호스트에서 codex login 진행 (~/.codex 토큰 발급)"
	@echo "  make codex-status   - 컨테이너 안 codex login status 확인"

dev:
	docker compose up -d
	@echo "API: http://localhost:8000 | Admin: http://localhost:3001"

demo: dev
	@echo "demo 모드 — COLLECTION_CRON_DEMO 가 적용되려면 .env 의 COLLECTION_CRON 을 COLLECTION_CRON_DEMO 값으로 swap"

migrate:
	docker compose exec api alembic upgrade head

create-admin:
	docker compose exec api python -m scripts.create_admin

import-cso:
	# A3 CSO 3.4 임포트 — ~14k 노드 + broad_interest 12 행 시드 (decision-backlog P1-5).
	# --refresh 플래그를 추가하려면: make import-cso ARGS=--refresh
	docker compose exec api python -m scripts.import_cso $(ARGS)

reset-password:
	@if [ -z "$(EMAIL)" ] || [ -z "$(NEW)" ]; then echo "EMAIL=... NEW=... 필요"; exit 1; fi
	docker compose exec api python -m scripts.reset_password --email "$(EMAIL)" --new-password "$(NEW)"

test:
	# codex v2 #8 → C-28: container 의 WORKDIR=/app 이고 Dockerfile 이 backend
	# context 를 직접 /app 으로 COPY → 컨테이너 안 path 는 /app/tests (backend/tests X).
	docker compose exec -e TESTING=1 api pytest tests -v

lint:
	docker compose exec api ruff check app/ scripts/
	docker compose exec api mypy --strict app/

check-all:
	python -m scripts.check_api_docs
	python -m scripts.check_schema
	python -m scripts.check_env
	python -m scripts.check_error_codes
	python -m scripts.check_redis_keys
	python -m scripts.check_contracts
	@echo "[OK] 6 cross-check 통과"

# v13 라운드 A4 Topic-driven Pivot prep — PASS-TO-PASS 회귀 그물.
# A4 본문 PR 직전 1회 통과 보장. 미포함:
#   - alembic downgrade -1 (사용자 결정으로 제외 — 1차 시연 forward-only)
#   - docker compose 부트 5분 idle sanity (사용자 결정으로 제외)
#   - make import-cso (5분 소요 — A4 PR baseline 직전 1회 manual 실행)
ci-regression-net:
	docker compose exec -e TESTING=1 api pytest tests -v --tb=short
	docker compose exec api ruff check app/ scripts/
	docker compose exec api mypy --strict app/
	python -m scripts.check_api_docs
	python -m scripts.check_schema
	python -m scripts.check_env
	python -m scripts.check_error_codes
	python -m scripts.check_redis_keys
	python -m scripts.check_contracts
	docker compose exec api alembic upgrade head
	@echo "[OK] PASS-TO-PASS 회귀 그물 green"

stop:
	docker compose stop

down:
	docker compose down

clean:
	@read -p "볼륨까지 삭제합니다 (Postgres + Redis 데이터 손실). 계속하시려면 'yes' 입력: " ans; \
	if [ "$$ans" = "yes" ]; then docker compose down -v; else echo "취소"; fi

# CodexOAuthProvider — 호스트 ~/.codex 인증 도우미 (2026-05-18).
# docker-compose 가 ~/.codex 를 backend 컨테이너에 volume mount 하므로 호스트에서
# 한 번 login 하면 컨테이너가 그대로 재사용. token refresh 도 codex CLI 가
# file lock 으로 자동 처리.
codex-login:
	@command -v codex >/dev/null 2>&1 || { \
		echo "[ERR] 호스트에 codex 미설치 — `npm install -g @openai/codex` 또는"; \
		echo "      `brew install --cask codex` 후 재시도"; exit 1; }
	codex login
	@echo "[OK] ~/.codex/auth.json 발급 완료. docker compose up 후 동일 토큰 재사용."

codex-status:
	@docker compose exec api codex login status || { \
		echo "[ERR] codex login 만료 또는 binary 없음 — `make codex-login` 후 재시도"; exit 1; }
