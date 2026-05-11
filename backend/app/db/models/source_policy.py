"""SourcePolicy ORM — schema.md SourcePolicy §, alembic 0001 source_policy 테이블.

3행 정확값 시드 (C-8): academic/vendor_blog=high, tech_news=medium. 모두 enabled=true,
collection_rule={} (JSONB dict 형식 — codex v2 #5).
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourcePolicy(Base):
    """source_type 별 신뢰도/수집 정책 SOR. fallback 룰 (FR-42·43) 진입."""

    __tablename__ = "source_policy"
    __table_args__ = (
        UniqueConstraint("source_category", name="uq_source_policy_category"),
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_category: Mapped[str] = mapped_column(String(40), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(10), nullable=False)
    collection_rule: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.true()
    )


__all__ = ["SourcePolicy"]
