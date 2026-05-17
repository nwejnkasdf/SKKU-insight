"""RecommendationSlot ORM — schema.md RecommendationSlot §, alembic 0006 recommendation_slot.

A8 (recommendation engine). 슬롯별 채움 상태 영속 (FR-37·42·43).

대시보드 1회 build 당 slot_type 별 1 row (core/adjacent/discovery 3 + fallback 1~2).
- target_count: 목표 카드 수 (core=5, adjacent=3, discovery=2)
- actual_count: 실제 채워진 카드 수
- fallback_reason: FR-42 (slot 부족) / FR-43 (전체 부족) 사유 텍스트

CHECK: slot_type IN ('core','adjacent','discovery','fallback_adjacent','fallback_trend')
       — contracts.SlotType enum SOR.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RecommendationSlot(Base):
    """슬롯별 채움 상태 — 대시보드 1회 build 당 slot 종류 별 1 row."""

    __tablename__ = "recommendation_slot"
    __table_args__ = (
        CheckConstraint(
            "slot_type IN ('core','adjacent','discovery','fallback_adjacent','fallback_trend')",
            name="ck_recommendation_slot_slot_type",
        ),
        Index(
            "ix_recommendation_slot_user_generated",
            "user_id",
            text("generated_at DESC"),
        ),
    )

    slot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    slot_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["RecommendationSlot"]
