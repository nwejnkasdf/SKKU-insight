"""Alembic env — async SQLAlchemy 2.x 패턴.

`target_metadata = Base.metadata` 가 모든 모델을 autogenerate 대상으로 등록.
A2 가 작성한 8 모델만 1번 migration 에 포함. 후속 에이전트는 자기 모델 추가 후
`alembic revision --autogenerate -m "..."` 로 다음 migration.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401 — Base.metadata 에 모델 등록

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# settings 의 DATABASE_URL 을 alembic config 에 동적 주입
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """offline 모드 — engine 없이 SQL 출력."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """async engine 으로 마이그레이션 실행."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
