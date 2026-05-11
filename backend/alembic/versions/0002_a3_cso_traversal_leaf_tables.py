"""A3 0002: CSOTopicParent + DynamicLeafTopic + DynamicLeafTopicCSOTopic 신규.

Revision ID: 0002_a3_cso_traversal_leaf
Revises: 0001_initial_a2
Create Date: 2026-05-11

A3 (CSO Topic Engine) 의 1번 migration. 신규 3 테이블:
- cso_topic_parent: CSO 다중 부모 M:N (composite PK). NetworkX 그래프 SOR.
- dynamic_leaf_topic: A7 가 본문 사용. A3 는 빈 테이블 + read-only endpoint.
- dynamic_leaf_topic_cso_topic: leaf ↔ cso_topic M:N.

**CSOTopic 컬럼은 손대지 않음**. `parent_topic_id` 는 deprecate 코멘트만 schema.md
에 추가, 컬럼 자체는 0003 이후 drop 예정 (다른 모듈 의존 코드 마이그레이션 완료 후).

JSONB seed 형식: 본 migration 은 빈 테이블만 생성. BroadInterest 12 행 시드는
A3 가 `scripts/import_cso.py` 가 `backend/app/config/broad_interests.toml` 로 INSERT
(`ON CONFLICT (name) DO UPDATE`).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_a3_cso_traversal_leaf"
down_revision: str | None = "0001_initial_a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ============================================================
    # 1. CSOTopicParent — CSO 다중 부모 M:N (A3 결정 5)
    # ============================================================
    op.create_table(
        "cso_topic_parent",
        sa.Column(
            "cso_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "parent_cso_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    # composite PK 가 자식 → 부모 lookup 빠름. 부모 → 자식 lookup 별도 인덱스.
    op.create_index(
        "ix_cso_topic_parent_parent",
        "cso_topic_parent",
        ["parent_cso_topic_id"],
    )

    # ============================================================
    # 2. DynamicLeafTopic — 사용자 동적 리프. A7 가 본문, A3 는 빈 테이블.
    # ============================================================
    op.create_table(
        "dynamic_leaf_topic",
        sa.Column(
            "leaf_topic_id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("label_en", sa.String(255), nullable=True),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_active_day", sa.Integer, nullable=False),
        sa.Column("last_signal_active_day", sa.Integer, nullable=False),
        sa.Column(
            "merged_into_leaf_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "dynamic_leaf_topic.leaf_topic_id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('emerging','active','stale','merged','archived')",
            name="ck_dynamic_leaf_topic_status",
        ),
    )
    op.create_index(
        "ix_dynamic_leaf_topic_user_status",
        "dynamic_leaf_topic",
        ["user_id", "status"],
    )

    # ============================================================
    # 3. DynamicLeafTopicCSOTopic — leaf ↔ cso_topic M:N
    # ============================================================
    op.create_table(
        "dynamic_leaf_topic_cso_topic",
        sa.Column(
            "leaf_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "dynamic_leaf_topic.leaf_topic_id", ondelete="CASCADE"
            ),
            primary_key=True,
        ),
        sa.Column(
            "cso_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cso_topic.cso_topic_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("dynamic_leaf_topic_cso_topic")
    op.drop_index(
        "ix_dynamic_leaf_topic_user_status",
        table_name="dynamic_leaf_topic",
    )
    op.drop_table("dynamic_leaf_topic")
    op.drop_index(
        "ix_cso_topic_parent_parent",
        table_name="cso_topic_parent",
    )
    op.drop_table("cso_topic_parent")
