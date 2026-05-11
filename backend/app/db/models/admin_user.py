"""AdminUser ORM — schema.md AdminUser §, alembic 0001 admin_user 테이블.

role CHECK (`super`/`operator`/`read_only`) + status (`active`/`disabled` 등 schema.md 참조).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminUser(Base):
    """관리자. SUPER/OPERATOR/READ_ONLY 3 role. must_change_password 강제 변경 흐름."""

    __tablename__ = "admin_user"
    __table_args__ = (
        UniqueConstraint("email", name="uq_admin_user_email"),
        CheckConstraint(
            "role IN ('super','operator','read_only')",
            name="ck_admin_user_role",
        ),
    )

    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["AdminUser"]
