"""C-53 (2026-05-24) weekly promotion + Recommendation origin metadata.

변경:
1. ck_collection_job_type CHECK 갱신 — 'weekly_promotion' 추가 (7-value → 8-value).
2. recommendation 테이블에 origin_type / origin_ref 컬럼 신규 (promotion 추적).
   - origin_type: 'reincarnation' | 'fusion' | NULL (core/adjacent/trend = NULL)
   - origin_ref: UUID (Reincarnation = trace_id, Fusion = bridge_cso_topic_id)

forward-only. downgrade 차단 (기존 row CHECK violation 위험).

Revision: 0010
Revises: 0009
Create Date: 2026-05-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_c53_weekly_promotion"
down_revision = "0009_p1_12_active_day_at_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ============================================================
    # 1. ck_collection_job_type 8-value (weekly_promotion 추가)
    # ============================================================
    op.drop_constraint(
        "ck_collection_job_type", "collection_job", type_="check"
    )
    op.create_check_constraint(
        "ck_collection_job_type",
        "collection_job",
        "job_type IN ('daily_collect','leaf_lifecycle','merge_evaluation',"
        "'summary_generation','interest_decay','trace_merge',"
        "'daily_user_profile_generation','weekly_promotion')",
    )

    # ============================================================
    # 2. recommendation 테이블 origin metadata 컬럼 신규
    # ============================================================
    # 사용자 save 시 weekly_promotion_job 이 origin 추적해서 promotion 결정.
    # origin_type 값:
    #   - 'reincarnation' = origin_ref 가 archived trace_id (status archived→active)
    #   - 'fusion'        = origin_ref 가 bridge_cso_topic_id (새 active trace INSERT)
    #   - NULL            = core / adjacent / fallback_* (promotion 대상 아님)
    # NULL 허용 — backward-compat (기존 row 영향 X).
    op.add_column(
        "recommendation",
        sa.Column("origin_type", sa.String(40), nullable=True),
    )
    op.add_column(
        "recommendation",
        sa.Column("origin_ref", sa.UUID(as_uuid=True), nullable=True),
    )
    # weekly_promotion_job 의 save event JOIN 효율 위해 partial index.
    op.create_index(
        "ix_recommendation_origin",
        "recommendation",
        ["origin_type", "origin_ref"],
        postgresql_where=sa.text("origin_type IS NOT NULL"),
    )


def downgrade() -> None:
    """forward-only 정책. C-42 0007 의 lesson — 기존 row CHECK violation 차단."""
    raise NotImplementedError(
        "0010 downgrade 차단: ck_collection_job_type 7-value 복원 전 weekly_promotion "
        "row DELETE / UPDATE 수동 필요. recommendation.origin_type / origin_ref 컬럼은 "
        "별개 — DROP COLUMN 자체 가능하지만 promotion 메타 손실. 운영자가 명시 의도 "
        "확인 후 직접 SQL 으로 처리."
    )
