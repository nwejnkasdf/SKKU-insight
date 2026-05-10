"""DB 인프라 — Base, async engine, AsyncSessionLocal, models.

모듈 책임 (sdd/module-boundaries.md `app/db`):
- SQLAlchemy 2.x async Engine 생성 (api/worker pool size 분리)
- AsyncSessionLocal 팩토리
- DeclarativeBase 단일 정의
- 8 모델 (User, AdminUser, UserConsent, UserCSOTraversal, BroadInterest,
  CSOTopic, Source, SourcePolicy) — A2 범위. 나머지 13 모델은 후속 에이전트.
"""
from __future__ import annotations

from app.db.base import Base
from app.db.engine import get_engine
from app.db.session import AsyncSessionLocal, get_session

__all__ = ["AsyncSessionLocal", "Base", "get_engine", "get_session"]
