"""DocumentTopic ORM — schema.md DocumentTopic §, alembic 0003 document_topic 테이블.

Document ↔ (cso_topic | leaf_topic) M:N 매핑. v13 라운드 (2026-05-11) 결정:
- CSO topic 매핑 = 자동 해결 (검색 query 자체가 topic). cso_topic_id = leaf 부모 cso_topic.
- leaf_topic_id 는 A7 leaf 데이터 채워진 후. A4 fallback 경로 (onboarding cluster) 는 leaf_topic_id=NULL.

CHECK: cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL — 둘 중 하나는 채움.
부분 UNIQUE 인덱스 3종 (cso_only / leaf_only / pair) 는 alembic raw SQL.
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentTopic(Base):
    """Document ↔ topic 매핑. 1차 시연은 cso_topic_id 단독 (leaf 는 A7 후)."""

    __tablename__ = "document_topic"
    __table_args__ = (
        CheckConstraint(
            "cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL",
            name="ck_document_topic_at_least_one_topic",
        ),
        Index("ix_document_topic_document", "document_id"),
        Index("ix_document_topic_cso", "cso_topic_id"),
        Index("ix_document_topic_leaf", "leaf_topic_id"),
        # (Codex round 2 S-07) alembic 0003 의 partial UNIQUE INDEX 3종 미러링.
        # `DocumentTopic upsert` (C-02) 의 ON CONFLICT target.
        Index(
            "ux_document_topic_cso_only",
            "document_id",
            "cso_topic_id",
            unique=True,
            postgresql_where=text(
                "leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_document_topic_leaf_only",
            "document_id",
            "leaf_topic_id",
            unique=True,
            postgresql_where=text(
                "cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_document_topic_pair",
            "document_id",
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
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document.document_id", ondelete="CASCADE"),
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
    confidence: Mapped[float] = mapped_column(Float, nullable=False)


__all__ = ["DocumentTopic"]
