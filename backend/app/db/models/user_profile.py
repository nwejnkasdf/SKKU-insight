"""UserProfile ORM — schema.md UserProfile §, alembic 0007 user_profile.

A8-v2 (UserProfile + Discovery Fusion + Reincarnation pivot) 라운드. 사용자별 1 row.
daily LLM cron (19 UTC) 가 사용자의 active + archived (score_tail >= 0.6) trace 를 입력으로
받아 (1) 캐릭터 요약 3 텍스트 + (2) fusion_candidates / deepening_seeds / broadening_seeds
3 JSONB array 생성. discovery slot 2 (Fusion + Reincarnation) 의 input SOR.

노출 정책: ORM/schema 만 정의, endpoint 부재 (A8-v2 결정 #4). 향후 노출 결정 시 endpoint 추가.

ID 매핑 가드: fusion_candidates 의 `bridge_cso_topic_id` / seeds 의 `cso_topic_id` 는
cso_graph 안에 있어야 한다 — generate_profile_payload 가 매핑 시점 가드 (graph 부재 시
candidate 제거). 본 테이블은 free-form JSONB 라 ORM 레벨 검증 없음 — application 레벨 가드.

NFR-04 정합: 본 테이블 자체는 사용자 화면에 노출 안 됨 (admin 도 endpoint 부재). discovery
카드의 reason_short 만 노출되며, 그 한 줄은 시간/강도 추상화 (점수·확률·버킷 키워드 거부).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserProfile(Base):
    """사용자별 캐릭터 프로파일 — daily LLM cron 갱신. PK=user_id (1:1)."""

    __tablename__ = "user_profile"
    __table_args__ = (
        Index("ix_user_profile_generated_at", "generated_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    recent_signals_summary: Mapped[str | None] = mapped_column(
        String(400), nullable=True
    )
    persistent_tendencies_summary: Mapped[str | None] = mapped_column(
        String(400), nullable=True
    )
    likely_dislikes_summary: Mapped[str | None] = mapped_column(
        String(400), nullable=True
    )
    # fusion_candidates: list[{from_archived, from_active, bridge_label,
    #   bridge_cso_topic_id, bridge_reasoning}] (0-3개).
    fusion_candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    # deepening_seeds / broadening_seeds: list[{cso_topic_id, label}] (0-3개).
    deepening_seeds: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    broadening_seeds: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    generator_version: Mapped[str] = mapped_column(String(20), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["UserProfile"]
