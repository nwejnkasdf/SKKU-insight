"""UserInterestState ORM — schema.md UserInterestState §, alembic 0004 user_interest_state.

A6 Beta-Bernoulli 사후 (단·장기) row 당 1개 (user, topic) 쌍. partial UNIQUE 3종 미러.
boost_applied_at_active_day 컬럼은 14-day onboarding boost 만료 cron 추적용 (NULL = 일반 row).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserInterestState(Base):
    """A6 (user, topic) 쌍의 베이지안 사후. atomic UPSERT 의 대상."""

    __tablename__ = "user_interest_state"
    __table_args__ = (
        CheckConstraint(
            "cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL",
            name="ck_user_interest_state_at_least_one_topic",
        ),
        Index("ix_user_interest_state_user", "user_id"),
        # alembic 0004 의 partial UNIQUE 3종 미러 — atomic UPSERT 의 ON CONFLICT target.
        Index(
            "ux_user_interest_state_cso_only",
            "user_id",
            "cso_topic_id",
            unique=True,
            postgresql_where=text(
                "leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_user_interest_state_leaf_only",
            "user_id",
            "leaf_topic_id",
            unique=True,
            postgresql_where=text(
                "cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_user_interest_state_pair",
            "user_id",
            "cso_topic_id",
            "leaf_topic_id",
            unique=True,
            postgresql_where=text(
                "cso_topic_id IS NOT NULL AND leaf_topic_id IS NOT NULL"
            ),
        ),
    )

    state_id: Mapped[uuid.UUID] = mapped_column(
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
    long_alpha: Mapped[float] = mapped_column(Float, nullable=False)
    long_beta: Mapped[float] = mapped_column(Float, nullable=False)
    short_alpha: Mapped[float] = mapped_column(Float, nullable=False)
    short_beta: Mapped[float] = mapped_column(Float, nullable=False)
    long_score: Mapped[float] = mapped_column(Float, nullable=False)
    short_score: Mapped[float] = mapped_column(Float, nullable=False)
    last_event_active_day: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_decay_active_day: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    boost_applied_at_active_day: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = ["UserInterestState"]
