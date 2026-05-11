"""DynamicLeafTopic ORM — schema.md DynamicLeafTopic §, alembic 0002 dynamic_leaf_topic 테이블.

사용자 동적 리프 토픽. A7 (leaf-lifecycle + traversal) 가 본문 작성·status 전이·LLM
재배치. A3 는 빈 테이블 + read-only endpoint (`GET /topics/leaves`, `/leaves/{id}`).
A3 시점에는 데이터 없으므로 endpoint 는 빈 PagedResponse 또는 404 응답.

CHECK: status IN ('emerging','active','stale','merged','archived') — LeafTopicStatus enum.
인덱스: (user_id, status) — 사용자별 status 필터링 가속.

Active day 기반 라이프사이클 (algorithms/cso-topic-traversal.md §5, leaf-topic-lifecycle.md).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DynamicLeafTopic(Base):
    """동적 리프 토픽. user-scoped. emerging→active→stale→merged|archived 라이프사이클."""

    __tablename__ = "dynamic_leaf_topic"
    __table_args__ = (
        CheckConstraint(
            "status IN ('emerging','active','stale','merged','archived')",
            name="ck_dynamic_leaf_topic_status",
        ),
        Index(
            "ix_dynamic_leaf_topic_user_status",
            "user_id",
            "status",
        ),
    )

    leaf_topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    label_en: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_active_day: Mapped[int] = mapped_column(Integer, nullable=False)
    last_signal_active_day: Mapped[int] = mapped_column(Integer, nullable=False)
    merged_into_leaf_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = ["DynamicLeafTopic"]
