"""Recommendation ORM — schema.md Recommendation §, alembic 0006 recommendation.

A8 (recommendation engine) phase 2 후반. 대시보드 10 카드 영속.

CHECK: slot_type IN ('core','adjacent','discovery','fallback_adjacent','fallback_trend')
       — contracts.SlotType enum SOR.

daily UNIQUE: (user_id, document_id, slot_type, (created_at AT TIME ZONE 'UTC')::date)
              — FR-28 같은 일자 중복 추천 차단. alembic raw SQL (functional index).
              ORM 메타에 `Index(unique=True, postgresql_where=text(...))` 미러링 시도해도
              functional expression 은 SQLAlchemy autogenerate 가 정확히 감지 못 함 →
              본 ORM 은 column · 비-functional index 만 정의 + alembic check 시 functional
              index 는 manual 검증 (A4 S-07 lesson 응용).

NFR-04 마스킹: score 컬럼 영속 (admin 노출 가능) 하지만 일반 사용자 응답 `RecommendationCard`
              schema 에는 field 부재 — 응답 변환 시 명시 매핑 (no `**row`).
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
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Recommendation(Base):
    """대시보드 추천 카드 영속 — slot_type 별 1 row."""

    __tablename__ = "recommendation"
    __table_args__ = (
        CheckConstraint(
            "slot_type IN ('core','adjacent','discovery','fallback_adjacent','fallback_trend')",
            name="ck_recommendation_slot_type",
        ),
        Index(
            "ix_recommendation_user_created",
            "user_id",
            text("created_at DESC"),
        ),
        Index("ix_recommendation_document", "document_id"),
    )

    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document.document_id", ondelete="CASCADE"),
        nullable=False,
    )
    slot_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # NFR-04: admin 노출 가능, 일반 사용자 응답 schema 미포함.
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # (C-53, 2026-05-24, alembic 0010) promotion 추적 metadata.
    # origin_type: 'reincarnation' | 'fusion' | NULL (core/adjacent/trend = NULL)
    # origin_ref: Reincarnation = archived trace_id / Fusion = bridge_cso_topic_id
    # weekly_promotion_job 가 본 컬럼 기준으로 promotion (status archived→active /
    # 새 active trace INSERT).
    origin_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    origin_ref: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["Recommendation"]
