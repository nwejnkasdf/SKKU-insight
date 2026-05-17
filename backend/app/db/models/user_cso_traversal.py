"""UserCSOTraversal ORM — schema.md UserCSOTraversal §, alembic 0001 user_cso_traversal 테이블.

핵심 모델: **사용자 관심은 단일 노드가 아니라 path 자체가 하나의 관심 상태 객체**.
A7 (leaf-lifecycle + traversal) 가 본문 사용. A2/A3 는 테이블·CHECK·index + read-only.

CHECK:
- status IN ('active','stale','archived')  (TraversalStatus enum SOR)
- cardinality(path) >= 1  (decision-backlog C-12 — array_length 빈 배열 NULL 우회 차단)

GIN index on path → path 위 cso_topic 검색용 (A8 추천 ranking).

A7 (decisions.md §12, 2026-05-17) 가 도입한 trace merge operation 5 신규:
- merged_into_trace_id: 자기 FK (ondelete='SET NULL'). winner trace 로 merge 된
  loser trace 가 status='archived' + 본 컬럼 = winner_id 마킹. audit/recovery.
- partial index ix_user_cso_traversal_merged_into (WHERE NOT NULL). alembic 0005.
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
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserCSOTraversal(Base):
    """사용자 traversal trace. path = ordered list of cso_topic_id (root → 말단)."""

    __tablename__ = "user_cso_traversal"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','stale','archived')",
            name="ck_user_cso_traversal_status",
        ),
        CheckConstraint(
            "cardinality(path) >= 1",
            name="ck_user_cso_traversal_path_nonempty",
        ),
        Index(
            "ix_user_cso_traversal_user_status",
            "user_id",
            "status",
        ),
        Index(
            "ix_user_cso_traversal_path_gin",
            "path",
            postgresql_using="gin",
        ),
        # (A7 alembic 0005) trace merge audit — partial index, NULL row 제외.
        Index(
            "ix_user_cso_traversal_merged_into",
            "merged_into_trace_id",
            postgresql_where=text("merged_into_trace_id IS NOT NULL"),
        ),
    )

    trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    path: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_active_day: Mapped[int] = mapped_column(Integer, nullable=False)
    last_activity_active_day: Mapped[int] = mapped_column(Integer, nullable=False)
    score_tail: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    # (A7) trace merge — winner trace 로 합쳐진 loser trace 의 audit 마커.
    # 자기 FK (ondelete='SET NULL'): winner 가 archive 또는 삭제되더라도 본 row 보존.
    merged_into_trace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_cso_traversal.trace_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["UserCSOTraversal"]
