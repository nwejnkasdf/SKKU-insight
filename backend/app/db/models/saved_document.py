"""SavedDocument ORM — schema.md SavedDocument §, alembic 0004 saved_document.

A6 UI-05 저장 (사용자 명시 액션). composite PK (user_id, document_id).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SavedDocument(Base):
    """사용자가 명시 저장한 Document. UI-05 list 표시."""

    __tablename__ = "saved_document"
    __table_args__ = (
        Index(
            "ix_saved_document_user_saved",
            "user_id",
            text("saved_at DESC"),
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
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


__all__ = ["SavedDocument"]
