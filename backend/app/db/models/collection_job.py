"""CollectionJob ORM — schema.md CollectionJob §, alembic 0003 collection_job 테이블.

v13 라운드 Q4 결정: 단일 row + 내부 N leaf 순회.
- 1 user/run = 1 CollectionJob row.
- target_cso_topic_id / target_leaf_topic_id = NULL (통합 row).
- source_id = sentinel `llm_search` (alembic 0003 시드).
- failure_reason 은 partial failure summary (leaf 별 실패 통합 문자열).

CHECK:
- job_type IN ('daily_collect','leaf_lifecycle','merge_evaluation','summary_generation')
- status IN ('queued','running','succeeded','failed','skipped') — contracts.CollectionJobStatus

인덱스: (status, finished_at DESC), (user_id, finished_at DESC) — admin/user 히스토리 조회 최적.
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
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CollectionJob(Base):
    """수집 작업 상태. queued→running→succeeded|failed|skipped 단방향."""

    __tablename__ = "collection_job"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('daily_collect','leaf_lifecycle','merge_evaluation','summary_generation')",
            name="ck_collection_job_type",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','skipped')",
            name="ck_collection_job_status",
        ),
        Index(
            "ix_collection_job_status_finished",
            "status",
            "finished_at",
        ),
        Index(
            "ix_collection_job_user_finished",
            "user_id",
            "finished_at",
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=True,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source.source_id", ondelete="SET NULL"),
        nullable=True,
    )
    target_cso_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"),
        nullable=True,
    )
    target_leaf_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="SET NULL"),
        nullable=True,
    )
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["CollectionJob"]
