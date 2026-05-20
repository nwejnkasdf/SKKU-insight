"""P1-12 0009: user_event.active_day_at_event INTEGER NULL.

Revision ID: 0009_p1_12_active_day_at_event
Revises: 0008_c44_p2_27_28
Create Date: 2026-05-20

P1-12 (C-45 라운드) — trace_extend / leaf_lifecycle window 룰의 SOR 정합 fix.

A7 결정 매트릭스 + decisions.md §4 "모든 N일 임계는 active day 기준" 이 SOR 인데
trace_extend caller (daily_lifecycle_evaluation_job._evaluate_trace_extension_for_user)
가 wall-clock `occurred_at >= NOW() - INTERVAL '7 days'` 로 임시 짜였음. 본 컬럼이
이벤트 발생 시점 user.active_day_counter 스냅샷을 보존 — caller 는
`(user.active_day_counter - active_day_at_event) <= 7` 로 active day delta 사용.

forward-only 정책 (downgrade 차단). 기존 row 는 NULL — caller 가 NULL row 를 window
밖으로 취급 (= 카운트 제외) 해 backward-compat. ingest 가 신규 INSERT 부터 채움.

decision-backlog P1-12 (2026-05-20).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_p1_12_active_day_at_event"
down_revision: str | Sequence[str] | None = "0008_c44_p2_27_28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_event",
        sa.Column("active_day_at_event", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "0009 P1-12 forward-only — user_event.active_day_at_event 제거는 trace_extend "
        "caller 가 의존하므로 차단 (A7 결정 #14)."
    )
