"""DynamicLeafTopicCSOTopic ORM — schema.md DynamicLeafTopicCSOTopic §, alembic 0002 동명 테이블.

leaf ↔ cso_topic M:N (composite PK + confidence + linked_at). FR-16 (leaf 최소 1 CSO 매핑)
는 앱 레벨 invariant (A7 LifecycleEvaluator 가 보장).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DynamicLeafTopicCSOTopic(Base):
    """leaf ↔ cso_topic M:N + confidence + linked_at."""

    __tablename__ = "dynamic_leaf_topic_cso_topic"

    leaf_topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="CASCADE"),
        primary_key=True,
    )
    cso_topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cso_topic.cso_topic_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["DynamicLeafTopicCSOTopic"]
