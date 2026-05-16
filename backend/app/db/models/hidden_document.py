"""HiddenDocument ORM — schema.md HiddenDocument §, alembic 0004 hidden_document.

A6 사용자 명시 숨김. composite PK (user_id, document_id).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HiddenDocument(Base):
    """사용자가 명시 숨김 처리한 Document. 추천 후보에서 제외."""

    __tablename__ = "hidden_document"
    __table_args__ = (
        Index(
            "ix_hidden_document_user_hidden",
            "user_id",
            text("hidden_at DESC"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    hidden_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


__all__ = ["HiddenDocument"]
