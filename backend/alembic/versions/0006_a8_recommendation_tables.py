"""A8 0006: Recommendation + RecommendationSlot + DocumentSummaryCache.

Revision ID: 0006_a8_recommendation_tables
Revises: 0005_a7_traversal_merge
Create Date: 2026-05-17

A8 (recommendation engine) phase 2 후반. 3 신규 테이블:

1. recommendation: 대시보드 10 카드 영속. slot_type CHECK 5종.
   - score 컬럼 nullable — NFR-04 마스킹 (일반 사용자 응답 schema 미포함).
   - daily UNIQUE: (user_id, document_id, slot_type, created_at::date) → 같은
     일자 동일 (사용자, 문서, 슬롯) 중복 추천 차단 (FR-28).
2. recommendation_slot: 슬롯별 채움 상태 (target/actual/fallback_reason).
3. document_summary_cache: FR-51 섹션형 LLM 요약 캐시 (document_id 1:1).

forward-only 정책 (downgrade 는 테스트 정합용만).

decisions.md §13 (A8 라운드, 2026-05-17). decision-backlog C-40.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_a8_recommendation_tables"
down_revision: str | None = "0005_a7_traversal_merge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SLOT_TYPE_CHECK = (
    "slot_type IN ('core','adjacent','discovery','fallback_adjacent','fallback_trend')"
)


def upgrade() -> None:
    # ============================================================
    # 1. recommendation — 대시보드 카드 영속. slot_type CHECK 5종.
    # ============================================================
    op.create_table(
        "recommendation",
        sa.Column(
            "recommendation_id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot_type", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("score", sa.Float, nullable=True),  # NFR-04: admin 노출만
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(_SLOT_TYPE_CHECK, name="ck_recommendation_slot_type"),
    )
    op.create_index(
        "ix_recommendation_user_created",
        "recommendation",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_recommendation_document", "recommendation", ["document_id"]
    )
    # daily UNIQUE — 같은 일자 동일 (user, doc, slot) 중복 추천 차단 (FR-28).
    # created_at AT TIME ZONE 'UTC' 로 안정성 보장 (server timezone 변동 차단).
    op.execute(
        "CREATE UNIQUE INDEX ux_recommendation_user_doc_slot_day "
        "ON recommendation "
        "(user_id, document_id, slot_type, ((created_at AT TIME ZONE 'UTC')::date))"
    )

    # ============================================================
    # 2. recommendation_slot — 슬롯별 채움 상태 + fallback_reason.
    # ============================================================
    op.create_table(
        "recommendation_slot",
        sa.Column("slot_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot_type", sa.String(40), nullable=False),
        sa.Column("target_count", sa.Integer, nullable=False),
        sa.Column("actual_count", sa.Integer, nullable=False),
        sa.Column("fallback_reason", sa.String(255), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            _SLOT_TYPE_CHECK, name="ck_recommendation_slot_slot_type"
        ),
    )
    op.create_index(
        "ix_recommendation_slot_user_generated",
        "recommendation_slot",
        ["user_id", sa.text("generated_at DESC")],
    )

    # ============================================================
    # 3. document_summary_cache — FR-51 섹션형 LLM 요약 1:1 캐시.
    # ============================================================
    op.create_table(
        "document_summary_cache",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sections", postgresql.JSONB, nullable=False),
        sa.Column("reason_short", sa.String(255), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("model_used", sa.String(120), nullable=False),
        sa.Column("generator", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "generator IN ('llm','source_abstract')",
            name="ck_document_summary_cache_generator",
        ),
    )


def downgrade() -> None:
    # forward-only 정책 — 본 함수는 테스트 정합용만.
    op.drop_table("document_summary_cache")
    op.drop_index(
        "ix_recommendation_slot_user_generated", table_name="recommendation_slot"
    )
    op.drop_table("recommendation_slot")
    op.execute("DROP INDEX IF EXISTS ux_recommendation_user_doc_slot_day")
    op.drop_index("ix_recommendation_document", table_name="recommendation")
    op.drop_index("ix_recommendation_user_created", table_name="recommendation")
    op.drop_table("recommendation")
