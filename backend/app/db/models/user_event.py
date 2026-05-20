"""UserEvent ORM — schema.md UserEvent §, alembic 0004 user_event 테이블.

A6 행동 로그 audit. payload_hash 컬럼은 idempotency 200/409 분기용 (sha256[:32] 64 hex).
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserEvent(Base):
    """A6 행동 로그. event_type ∈ EventType enum. UNIQUE(user_id, client_request_id) idempotency."""

    __tablename__ = "user_event"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('view','click','dwell_tick','open_external','save','hide','not_interested')",
            name="ck_user_event_type",
        ),
        UniqueConstraint(
            "user_id", "client_request_id", name="uq_user_event_idempotency"
        ),
        Index(
            "ix_user_event_user_created",
            "user_id",
            text("created_at DESC"),
        ),
        Index("ix_user_event_type", "event_type"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document.document_id", ondelete="SET NULL"),
        nullable=True,
    )
    cso_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"),
        nullable=True,
    )
    leaf_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    dwell_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_request_id: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # P1-12 fix (alembic 0009, 2026-05-20): 이벤트 발생 시점 user.active_day_counter
    # 스냅샷. trace_extend / leaf_lifecycle 의 active day delta window 계산에 사용
    # (벽시계가 아닌 SRS 시간모델 SOR 정합). 0009 이전 row 는 NULL — caller 는
    # NULL row 를 window 밖으로 취급.
    active_day_at_event: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["UserEvent"]
