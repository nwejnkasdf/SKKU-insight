"""SQLAlchemy 2.x DeclarativeBase 단일 정의.

본 Base 가 모든 모델의 부모. Alembic env.py 가 `Base.metadata` 를 target_metadata 로 사용.
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy 2.x DeclarativeBase. 모든 모델은 본 Base 를 상속."""


__all__ = ["Base"]
