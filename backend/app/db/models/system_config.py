"""SystemConfig ORM — schema.md SystemConfig §, alembic 0004 system_config.

A6 가 도입한 시스템 설정값 SOR (interest_params + event_weights). A6 read-only,
A10 admin-console 가 PUT /admin/system-config 로 변경. lifespan startup 시
Redis SETEX 60s 로 캐싱 (read hot path).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemConfig(Base):
    """시스템 설정 (key, JSONB value)."""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admin_user.admin_id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = ["SystemConfig"]
