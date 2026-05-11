"""BroadInterest ORM — schema.md BroadInterest §, alembic 0001 broad_interest 테이블.

A2 는 빈 테이블만 생성. A3 가 `scripts/import_cso.py` 가 CSO 임포트 직후
`backend/app/config/broad_interests.toml` 의 12 entry 를 `ON CONFLICT (name) DO UPDATE`
로 시드. `cso_seed_topic_id` 는 cso-mapping.md SEEDS dict 의 cluster→full label
매핑을 cso_topic FK 로 resolve.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BroadInterest(Base):
    """12 CSO 클러스터를 사용자에 노출하는 BroadInterest. 온보딩 카테고리 (FR-08·13)."""

    __tablename__ = "broad_interest"
    __table_args__ = (
        UniqueConstraint("name", name="uq_broad_interest_name"),
    )

    broad_interest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cso_cluster_label: Mapped[str] = mapped_column(String(40), nullable=False)
    cso_seed_topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cso_topic.cso_topic_id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )


__all__ = ["BroadInterest"]
