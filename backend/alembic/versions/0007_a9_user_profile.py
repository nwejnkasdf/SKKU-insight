"""A9 0007: user_profile — daily LLM cron 이 생성하는 사용자별 캐릭터 + fusion seeds.

Revision ID: 0007_a9_user_profile
Revises: 0006_a8_recommendation_tables
Create Date: 2026-05-19

A8-v2 (UserProfile + Discovery Fusion + Reincarnation pivot) 라운드. 신규 1 테이블 + CHECK 갱신:

1. user_profile: 사용자별 1 row (PK=user_id, 1:1). daily 19 UTC cron 갱신.
   - 3 자유 텍스트 (recent / persistent / likely_dislikes 요약) + 3 JSONB array
     (fusion_candidates / deepening_seeds / broadening_seeds).
   - generator_version 으로 prompt template 변경 추적.

2. ck_collection_job_type CHECK 갱신 — 'daily_user_profile_generation' 추가.
   0005 의 6-value CHECK 를 7-value 로 교체. SOR (contracts.JobType) 정합 위해서만.
   실제 CollectionJob.job_type 으로 INSERT 되진 않음 (A8-v2 cron 은 scheduler 전용 — 다른
   JobType 항목 LEAF_LIFECYCLE / MERGE_EVALUATION / INTEREST_DECAY / TRACE_MERGE 도
   동일 패턴). cross-check `scripts/check_contracts.py` 가 CHECK clause ↔ enum 매핑
   검증.

forward-only 정책 (downgrade 는 테스트 정합용만).

decisions.md §15 (A8-v2 라운드, 2026-05-19). decision-backlog C-42.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_a9_user_profile"
down_revision: str | None = "0006_a8_recommendation_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ============================================================
    # 1. user_profile — 사용자별 1 row (PK=user_id).
    # ============================================================
    op.create_table(
        "user_profile",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("recent_signals_summary", sa.String(400), nullable=True),
        sa.Column("persistent_tendencies_summary", sa.String(400), nullable=True),
        sa.Column("likely_dislikes_summary", sa.String(400), nullable=True),
        sa.Column(
            "fusion_candidates",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "deepening_seeds",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "broadening_seeds",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("generator_version", sa.String(20), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_profile_generated_at", "user_profile", ["generated_at"]
    )

    # ============================================================
    # 2. ck_collection_job_type 갱신 — 'daily_user_profile_generation' 추가.
    # ============================================================
    # 0005 의 6-value CHECK 를 7-value 로 교체. JobType enum SOR 정합.
    op.drop_constraint(
        "ck_collection_job_type", "collection_job", type_="check"
    )
    op.create_check_constraint(
        "ck_collection_job_type",
        "collection_job",
        "job_type IN ('daily_collect','leaf_lifecycle','merge_evaluation',"
        "'summary_generation','interest_decay','trace_merge',"
        "'daily_user_profile_generation')",
    )


def downgrade() -> None:
    """forward-only 정책 — 본 함수는 호출 차단.

    Codex R1 Suggested #6 fix (2026-05-19): 6-value CHECK 재생성 시 기존 row 가
    `daily_user_profile_generation` 인 경우 CHECK violation 으로 실패. 본 함수는
    명시적으로 NotImplementedError raise — alembic downgrade 사용 금지.
    rollback 필요 시 운영자가 (1) 해당 row DELETE / UPDATE → 다른 job_type 매핑
    (2) 본 함수 임시 우회 (직접 SQL) 두 단계 수동 실행.
    """
    raise NotImplementedError(
        "alembic 0007 (A8-v2 UserProfile) downgrade 차단. "
        "ck_collection_job_type 6-value 복원 전 daily_user_profile_generation "
        "row 수동 정리 필요. 운영 rollback 은 SOP 별도."
    )
