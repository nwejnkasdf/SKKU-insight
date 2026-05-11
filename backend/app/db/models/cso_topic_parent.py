"""CSOTopicParent ORM — A3 도입, schema.md CSOTopicParent §, alembic 0002 cso_topic_parent 테이블.

CSO 다중 부모 (DAG) 보존용 M:N 연결 테이블. composite PK 로 동일 (자식, 부모) 페어
중복 방지. `ON CONFLICT DO NOTHING` idempotent INSERT.

NetworkX 그래프 빌드의 SOR — `build_cso_graph` 가 본 테이블만 사용하며 deprecate
중인 `CSOTopic.parent_topic_id` 는 무시한다 (A3 결정 18).

사이클 금지: 앱 레벨 `verify_cso_import` 가 `nx.is_directed_acyclic_graph` 보장.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CSOTopicParent(Base):
    """CSO 다중 부모 M:N. (cso_topic_id, parent_cso_topic_id) composite PK."""

    __tablename__ = "cso_topic_parent"
    __table_args__ = (
        # 부모 → 자식 lookup 가속 (composite PK 가 자식 → 부모 lookup 은 이미 빠름)
        Index("ix_cso_topic_parent_parent", "parent_cso_topic_id"),
    )

    cso_topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"),
        primary_key=True,
    )
    parent_cso_topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"),
        primary_key=True,
    )


__all__ = ["CSOTopicParent"]
