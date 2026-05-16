"""NotInterestedTopic ORM — schema.md NotInterestedTopic §, alembic 0004 not_interested_topic.

A6 명시 토픽 거부 마킹 (not-interested 하이브리드 — 최고 confidence 1건). partial UNIQUE 3종 미러.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotInterestedTopic(Base):
    """사용자가 명시 거부한 토픽. 추천 필터에 사용. cso/leaf 한쪽 NOT NULL."""

    __tablename__ = "not_interested_topic"
    __table_args__ = (
        CheckConstraint(
            "cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL",
            name="ck_not_interested_topic_at_least_one_topic",
        ),
        Index("ix_not_interested_topic_user", "user_id"),
        Index(
            "ux_not_interested_topic_cso_only",
            "user_id",
            "cso_topic_id",
            unique=True,
            postgresql_where=text(
                "leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_not_interested_topic_leaf_only",
            "user_id",
            "leaf_topic_id",
            unique=True,
            postgresql_where=text(
                "cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_not_interested_topic_pair",
            "user_id",
            "cso_topic_id",
            "leaf_topic_id",
            unique=True,
            postgresql_where=text(
                "cso_topic_id IS NOT NULL AND leaf_topic_id IS NOT NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    cso_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"),
        nullable=True,
    )
    leaf_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


__all__ = ["NotInterestedTopic"]
