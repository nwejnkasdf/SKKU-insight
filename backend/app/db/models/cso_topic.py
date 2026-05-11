"""CSOTopic ORM — schema.md CSOTopic §, alembic 0001 cso_topic 테이블.

`parent_topic_id` 는 **deprecate 예정** (A3 결정 18, 후속 0003 alembic 에서 drop).
다중 부모 (DAG) 는 `cso_topic_parent` M:N 테이블 (A3 도입) 이 SOR. 본 컬럼은
BFS 첫 부모 backward-compat 만 유지. 신규 코드는 cso_topic_parent 만 사용.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CSOTopic(Base):
    """CSO 3.4 토픽. ~14k 노드. A3 가 scripts/import_cso.py 로 임포트."""

    __tablename__ = "cso_topic"
    __table_args__ = (
        UniqueConstraint("uri", name="uq_cso_topic_uri"),
        Index("ix_cso_topic_label", "label"),
        Index("ix_cso_topic_parent", "parent_topic_id"),
        Index(
            "ix_cso_topic_cluster_labels",
            "cluster_labels",
            postgresql_using="gin",
        ),
    )

    cso_topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    uri: Mapped[str] = mapped_column(String(500), nullable=False)
    parent_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"),
        nullable=True,
    )
    cluster_labels: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, server_default="{}"
    )


__all__ = ["CSOTopic"]
