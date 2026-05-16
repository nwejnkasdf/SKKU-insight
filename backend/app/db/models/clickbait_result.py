"""ClickbaitResult ORM — schema.md ClickbaitResult §, alembic 0003 clickbait_result 테이블.

v13 라운드 결정: 1차 시연 default 비활성 (CLICKBAIT_ENABLED=false). schema 만 보존.
사용자가 News 소스 명시 활성화 시 A5 가 post-filter 로 INSERT.

CHECK: decision IN ('clickbait','clean','error') — contracts.ClickbaitDecision enum.
UNIQUE: document_id — 1 document = 1 result.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClickbaitResult(Base):
    """DoRA 분류기 응답 1건. 1차 비활성 — schema 보존만."""

    __tablename__ = "clickbait_result"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_clickbait_result_document"),
        CheckConstraint(
            "decision IN ('clickbait','clean','error')",
            name="ck_clickbait_result_decision",
        ),
    )

    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document.document_id", ondelete="CASCADE"),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(20), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


__all__ = ["ClickbaitResult"]
