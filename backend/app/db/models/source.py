"""Source ORM — schema.md Source §, alembic 0001 source 테이블.

sentinel source `cold_start_pseudo` 1행 시드 0001 migration 에 정확값 박힘
(cold-start.md §pseudo-document — pseudo Document FK 충족용).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Source(Base):
    """문서 수집 출처. arXiv/OpenAlex/RSS/네이버 BS4 등 + sentinel cold_start_pseudo."""

    __tablename__ = "source"
    __table_args__ = (
        UniqueConstraint("name", name="uq_source_name"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(10), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.true()
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )


__all__ = ["Source"]
