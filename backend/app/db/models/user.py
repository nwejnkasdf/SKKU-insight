"""User ORM — schema.md User §, alembic 0001 user 테이블.

functional UNIQUE LOWER(email) WHERE deleted_at IS NULL 은 0001 migration 의
raw SQL 로 직접 적용 (SQLAlchemy 가 functional+partial index 동시 표현 어려움).
본 모델 클래스는 UniqueConstraint 를 정의하지 않고 컬럼만 미러.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """일반 사용자. C-7 3겹 방어 (email 정규화) + active_day_counter (시간 모델 SOR).

    `email` 은 어플리케이션 계층에서 LOWER 정규화 (Pydantic validator + service
    계층 + DB functional partial UNIQUE index `ix_user_email`).
    """

    __tablename__ = "user"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.false()
    )
    active_day_counter: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_active_calendar_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["User"]
