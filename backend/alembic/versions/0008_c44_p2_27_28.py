"""C-44 0008: P2-27 archived_at_active_day + P2-28 candidate_pool_ids.

Revision ID: 0008_c44_p2_27_28
Revises: 0007_a9_user_profile
Create Date: 2026-05-19

P2 백로그 그룹 C 일부 (C-44 라운드). 두 컬럼 추가:

1. user_cso_traversal.archived_at_active_day INTEGER NULL (P2-27)
   - A7 execute_archive / execute_merge 가 archive 진입 시점 user.active_day_counter
     값을 저장. fix 전: last_activity_active_day 가 archive 직후 last_activity 와
     동일 → A8-v2 reincarnation gap_days_min 가드 의미 약화 (long idle 후 archived
     trace 가 직후 reincarnation 후보 즉시 적격).
   - fix 후: queries.get_top_archived_trace 가 archived_at_active_day 기준 cutoff →
     실제 archive 시점에서 gap_days 측정.
   - 기존 archived row 가 0건 (시연 환경) → NULL backfill OK. NULL 인 row 는
     queries 가 fallback (= last_activity_active_day) 으로 처리해 backward-compat.

2. user_profile.candidate_pool_ids JSONB NOT NULL DEFAULT '{}' (P2-28)
   - LLM 이 fusion bridge / deepening / broadening 선택 시 사용한 candidate_pool 의
     UUID list 를 카테고리별로 영속화. fix 전: validation 이 cso_graph 전체 멤버십만
     검사 (LLM hallucination 가능 — graph 의 임의 노드 선택). fix 후: 카테고리별
     pool 안에 있는지 검증.
   - 구조: {"fusion": [uuid_str, ...], "deepening": [...], "broadening": [...]}.
     서버 default '{}' 로 기존 row 안전.

forward-only 정책 (downgrade 차단).

decision-backlog C-44 (P2 백로그 그룹 C — P2-27 + P2-28, 2026-05-19).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_c44_p2_27_28"
down_revision: str | None = "0007_a9_user_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ============================================================
    # P2-27: user_cso_traversal.archived_at_active_day
    # ============================================================
    op.add_column(
        "user_cso_traversal",
        sa.Column(
            "archived_at_active_day",
            sa.Integer(),
            nullable=True,
        ),
    )
    # archive 시점 비교 성능 — get_top_archived_trace / get_archived_traces_with_score 의
    # ORDER BY archived_at_active_day DESC + WHERE archived_at_active_day <= cutoff.
    op.create_index(
        "ix_user_cso_traversal_archived_at_active_day",
        "user_cso_traversal",
        ["archived_at_active_day"],
    )

    # ============================================================
    # P2-28: user_profile.candidate_pool_ids
    # ============================================================
    op.add_column(
        "user_profile",
        sa.Column(
            "candidate_pool_ids",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    """forward-only 정책 — 본 함수는 호출 차단.

    candidate_pool_ids NOT NULL DEFAULT '{}' 라 단순 DROP 가능하나, archived_at_active_day
    가 채워진 row 가 존재 시 운영자 의도 확인 필요. 본 함수는 일관성 위해 차단.
    """
    raise NotImplementedError(
        "alembic 0008 (C-44 P2-27 + P2-28) downgrade 차단. "
        "rollback 필요 시 SOP 별도 — 운영자가 archived_at_active_day 채워진 row 영향 "
        "확인 + candidate_pool_ids JSONB 사용처 확인 후 수동 진행."
    )
