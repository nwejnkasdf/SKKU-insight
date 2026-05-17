"""A7 0005: UserCSOTraversal.merged_into_trace_id + ck_collection_job_type 갱신.

Revision ID: 0005_a7_traversal_merge
Revises: 0004_a6_interest_tables
Create Date: 2026-05-17

A7 (leaf-lifecycle + traversal) phase 1. trace merge operation 신규 도입에 따른 변경:

1. user_cso_traversal 에 merged_into_trace_id 컬럼 추가 (self-FK, ondelete='SET NULL').
   - winner trace 로 merge 된 loser trace 가 status='archived' + 본 컬럼 = winner_id.
   - audit/recovery 용 — `DynamicLeafTopic.merged_into_leaf_topic_id` 패턴 동일.
   - partial index ix_user_cso_traversal_merged_into (WHERE merged_into_trace_id IS NOT NULL).

2. ck_collection_job_type CHECK 갱신 — 'trace_merge' 추가.
   - 0003 의 4-value CHECK + 0004 가 추가 안 한 'interest_decay' 도 같이 추가
     (A6 P2-21 backlog 해소).
   - 최종 6-value: daily_collect / leaf_lifecycle / merge_evaluation /
     summary_generation / interest_decay / trace_merge.

JSONB seed 는 변경 없음. forward-only 정책 (downgrade 는 테스트 정합용만).

A7 결정 매트릭스 #9 (alembic 0005 필요) + #17 (trace merge 신규 도입).
decisions.md §12 (A7 라운드, 2026-05-17).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_a7_traversal_merge"
down_revision: str | None = "0004_a6_interest_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ============================================================
    # 1. user_cso_traversal.merged_into_trace_id — trace merge audit
    # ============================================================
    op.add_column(
        "user_cso_traversal",
        sa.Column(
            "merged_into_trace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "user_cso_traversal.trace_id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
    )
    # partial index — merged 만 인덱스 (대부분 row 가 NULL 이라 cardinality 낮음).
    op.execute(
        "CREATE INDEX ix_user_cso_traversal_merged_into "
        "ON user_cso_traversal (merged_into_trace_id) "
        "WHERE merged_into_trace_id IS NOT NULL"
    )

    # ============================================================
    # 2. ck_collection_job_type 갱신 — interest_decay + trace_merge 추가
    # ============================================================
    # 0003 의 4-value CHECK 를 6-value 로 교체. 0004 가 interest_decay 를 명시 추가하지
    # 않았던 P2-21 backlog 도 본 단계에서 같이 해소.
    op.drop_constraint(
        "ck_collection_job_type", "collection_job", type_="check"
    )
    op.create_check_constraint(
        "ck_collection_job_type",
        "collection_job",
        "job_type IN ('daily_collect','leaf_lifecycle','merge_evaluation',"
        "'summary_generation','interest_decay','trace_merge')",
    )


def downgrade() -> None:
    # forward-only 정책 — 본 함수는 테스트 정합용만.
    op.drop_constraint(
        "ck_collection_job_type", "collection_job", type_="check"
    )
    op.create_check_constraint(
        "ck_collection_job_type",
        "collection_job",
        "job_type IN ('daily_collect','leaf_lifecycle','merge_evaluation',"
        "'summary_generation')",
    )
    op.execute("DROP INDEX IF EXISTS ix_user_cso_traversal_merged_into")
    op.drop_column("user_cso_traversal", "merged_into_trace_id")
