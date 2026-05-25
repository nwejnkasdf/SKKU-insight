"""C-62 (2026-05-25) bootstrap trace + recommendation_score + softmax core fill.

변경:
1. document_topic.recommendation_score INTEGER NULL — LLM-as-judge 1~10 점수
   (LLM 이 수집 pool 내부 상대 평가). NULL = 미평가 (옛 row 또는 LLM 미응답).
2. user_cso_traversal.origin VARCHAR(32) NULL — trace 출처 구분:
   - 'onboarding_boost' = bootstrap_interest_state 가 사용자 선택 cluster 마다 INSERT
   - 'behavioral'       = ingest_event_atomic click/save/dwell_tick hook 이 INSERT
   - 'weekly_promotion' = weekly_promotion_job 이 Reincarnation/Fusion bridge INSERT
   - NULL               = 옛 row (구현 전 trace, default 'behavioral' 로 간주)

forward-only. downgrade 차단 (의미 손실).

Revision: 0011
Revises: 0010
Create Date: 2026-05-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_c62_recommendation_score"
down_revision = "0010_c53_weekly_promotion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ============================================================
    # 1. document_topic.recommendation_score INTEGER NULL
    # ============================================================
    # LLM 수집 prompt 가 leaf 단위 후보 풀 안에서 doc 별 1~10 점수 산출
    # (`recommendation_score`). Core slot fill 이 trace softmax → 그 trace 영역
    # 내 doc 중 max(recommendation_score) 선택.
    op.add_column(
        "document_topic",
        sa.Column("recommendation_score", sa.Integer(), nullable=True),
    )
    # CHECK 1~10 — LLM-as-judge 상대값.
    op.create_check_constraint(
        "ck_document_topic_recommendation_score_range",
        "document_topic",
        "recommendation_score IS NULL OR (recommendation_score BETWEEN 1 AND 10)",
    )

    # ============================================================
    # 2. user_cso_traversal.origin VARCHAR(32) NULL
    # ============================================================
    op.add_column(
        "user_cso_traversal",
        sa.Column("origin", sa.String(32), nullable=True),
    )
    op.create_check_constraint(
        "ck_user_cso_traversal_origin",
        "user_cso_traversal",
        "origin IS NULL OR origin IN "
        "('onboarding_boost', 'behavioral', 'weekly_promotion')",
    )
    # 옛 row backfill — 'behavioral' (이전 구현은 click hook 만 trace 생성).
    op.execute(
        "UPDATE user_cso_traversal SET origin = 'behavioral' WHERE origin IS NULL"
    )
    # 'onboarding_boost' 분리 색인 — _is_cold_start 가 behavioral 만 카운트 위해
    # 매 dashboard 호출 검사. partial index 로 sparse / fast.
    op.create_index(
        "ix_user_cso_traversal_origin_boost",
        "user_cso_traversal",
        ["user_id"],
        postgresql_where=sa.text("origin = 'onboarding_boost' AND status = 'active'"),
    )


def downgrade() -> None:
    # forward-only.
    raise NotImplementedError(
        "0011_c62_recommendation_score: downgrade 비지원 — 옛 데이터 의미 손실 위험"
    )
