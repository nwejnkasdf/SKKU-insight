"""UserConsent ORM — schema.md UserConsent §, alembic 0001 user_consent 테이블.

consent_type 별 행이 다중. revoked_at NULL 이면 active.
복합 인덱스 (user_id, consent_type) 으로 active consent lookup 최적화.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserConsent(Base):
    """사용자 동의 기록. consent.service 가 active 여부 cache 60s."""

    __tablename__ = "user_consent"
    __table_args__ = (
        Index("ix_user_consent_user_type", "user_id", "consent_type"),
    )

    consent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_type: Mapped[str] = mapped_column(String(40), nullable=False)
    agreed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["UserConsent"]
