# backend Dockerfile — python:3.12-slim
# A2 Phase 0b. uvicorn + RQ worker 동일 이미지 (docker-compose 가 command 로 분기).

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        ca-certificates \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# Codex CLI (CodexOAuthProvider 본문, 2026-05-18). LLM_PROVIDER=codex_oauth 토글
# 시 lifespan 이 `codex --version` 검증. Node.js 20 LTS + `@openai/codex`.
# `~/.codex` 는 docker-compose 가 volume mount (호스트 OAuth 토큰 재사용).
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @openai/codex \
    && codex --version

WORKDIR /app

# 의존 먼저 (캐시 활용)
COPY pyproject.toml /app/
RUN pip install --upgrade pip && pip install -e ".[dev]" "psycopg[binary]>=3.1"

COPY . /app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
