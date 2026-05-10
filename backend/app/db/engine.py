"""Async SQLAlchemy engine — api/worker pool size 분리.

api 컨테이너 = `PG_API_POOL_MIN/MAX` (5/30). worker 컨테이너 = `PG_WORKER_POOL_MIN/MAX` (2/10).
worker 가 api 요청을 굶기지 않도록 (concurrency.md §1).
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings

PoolMode = Literal["api", "worker"]

_engines: dict[PoolMode, AsyncEngine] = {}


def get_engine(mode: PoolMode = "api") -> AsyncEngine:
    """모드별 단일 engine 인스턴스 반환. lifespan 에서 생성·dispose."""
    if mode in _engines:
        return _engines[mode]
    settings = get_settings()
    if mode == "api":
        pool_size = settings.PG_API_POOL_MIN
        max_overflow = settings.PG_API_POOL_MAX - settings.PG_API_POOL_MIN
    else:
        pool_size = settings.PG_WORKER_POOL_MIN
        max_overflow = settings.PG_WORKER_POOL_MAX - settings.PG_WORKER_POOL_MIN
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        echo=False,
    )
    _engines[mode] = engine
    return engine


async def dispose_engines() -> None:
    """lifespan shutdown 에서 호출. 모든 engine 의 connection 닫음."""
    for engine in _engines.values():
        await engine.dispose()
    _engines.clear()


__all__ = ["PoolMode", "dispose_engines", "get_engine"]
