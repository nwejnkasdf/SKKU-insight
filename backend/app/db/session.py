"""AsyncSessionLocal + FastAPI Depends.

`get_session()` 은 FastAPI Depends 로 endpoint 에 주입. 세션 누수 방지를 위해 context manager
패턴 사용.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.engine import get_engine

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=get_engine("api"),
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI Depends. 각 요청마다 새 세션 생성·종료."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


__all__ = ["AsyncSessionLocal", "get_session"]
