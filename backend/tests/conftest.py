"""pytest conftest — docker-compose 실 postgres + redis 사용.

- 세션 fixture: 별도 test DB `insight_test` 생성 + Alembic upgrade head 1회
- 함수 fixture:
  - `db_session`: BEGIN → ROLLBACK (각 테스트 격리)
  - `redis`: flushdb (Redis DB 분리 — 보통 5번 DB 사용 권장이나 본 conftest 는 default 0 사용)
  - `client`: httpx.AsyncClient(transport=ASGITransport(app))
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


# ============================================================
# 1. TESTING env 셋팅 — backend.app.config 가 로드되기 전에
# ============================================================

os.environ["TESTING"] = "1"
# CI / 로컬 docker-compose 가 postgres+redis 띄운 상태 가정
os.environ.setdefault("POSTGRES_PASSWORD", "changeme-strong-password")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://insight:changeme-strong-password@localhost:5432/insight_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("REDIS_URL_RATE_LIMIT", "redis://localhost:6379/14")
os.environ.setdefault("REDIS_URL_QUEUE", "redis://localhost:6379/13")
os.environ.setdefault("REDIS_URL_CACHE", "redis://localhost:6379/12")
os.environ.setdefault("JWT_SECRET", "x" * 64)
os.environ.setdefault("BCRYPT_COST", "4")  # 테스트 속도 — bcrypt 4 라운드


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: 통합 테스트 (docker-compose 의존)")


# ============================================================
# 2. event_loop 세션 스코프
# ============================================================


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ============================================================
# 3. DB engine + Alembic migrate
# ============================================================


@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """test DB 생성 + Alembic upgrade head."""
    from app.config import get_settings

    settings = get_settings()
    # admin DB 로 접속해 insight_test 가 없으면 생성
    admin_url = settings.DATABASE_URL.replace("/insight_test", "/postgres")
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        result = await conn.exec_driver_sql(
            "SELECT 1 FROM pg_database WHERE datname='insight_test'"
        )
        if result.fetchone() is None:
            await conn.exec_driver_sql('CREATE DATABASE insight_test')
    await admin_engine.dispose()

    # Alembic migrate
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    command.upgrade(cfg, "head")

    engine = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=2)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """함수 단위 트랜잭션 — 종료 시 ROLLBACK 으로 격리."""
    async with db_engine.connect() as connection:
        trans = await connection.begin()
        session_factory = async_sessionmaker(
            bind=connection, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory() as session:
            yield session
        await trans.rollback()


# ============================================================
# 4. Redis fixture — 매 테스트 flushdb
# ============================================================


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[aioredis.Redis]:
    from app.config import get_settings

    settings = get_settings()
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


# ============================================================
# 5. HTTP client — ASGITransport
# ============================================================


@pytest_asyncio.fixture
async def client(
    db_engine: AsyncEngine, redis_client: aioredis.Redis
) -> AsyncIterator[AsyncClient]:
    """app.main:app 의 ASGI transport. lifespan 은 fixture 가 직접 init/dispose
    하므로 ASGITransport 의 lifespan='off' 사용."""
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app, lifespan="off")
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        yield ac
