"""A2 initial: User/AdminUser/UserConsent/UserCSOTraversal/BroadInterest/CSOTopic/Source/SourcePolicy

Revision ID: 0001_initial_a2
Revises:
Create Date: 2026-05-11

Phase 0b A2 의 1번 migration. schema.md + decision-backlog.md C-7~9 정합:
- User: functional UNIQUE LOWER(email) WHERE deleted_at IS NULL (C-7)
- UserCSOTraversal: CHECK 하한만 (path 상한 8 은 A7 앱 레벨, schema.md §UserCSOTraversal)
- Source sentinel `cold_start_pseudo` 1행 시드 (cold-start.md §pseudo-document)
- SourcePolicy 3행 정확값 시드 (C-8): academic/vendor_blog=high, tech_news=medium, rule={}, enabled=true

빈 테이블 (다른 에이전트가 시드):
- CSOTopic (A3 가 CSO 3.4 임포트)
- BroadInterest (A3 가 12행 시드, cso_seed_topic_id FK 의존)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_a2"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ============================================================
    # 1. CSOTopic (빈 테이블 — A3 가 임포트)
    # ============================================================
    op.create_table(
        "cso_topic",
        sa.Column("cso_topic_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("uri", sa.String(500), nullable=False),
        sa.Column(
            "parent_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "cluster_labels",
            postgresql.ARRAY(sa.String(40)),
            nullable=False,
            server_default="{}",
        ),
        sa.UniqueConstraint("uri", name="uq_cso_topic_uri"),
    )
    op.create_index("ix_cso_topic_label", "cso_topic", ["label"])
    op.create_index("ix_cso_topic_parent", "cso_topic", ["parent_topic_id"])
    op.create_index(
        "ix_cso_topic_cluster_labels",
        "cso_topic",
        ["cluster_labels"],
        postgresql_using="gin",
    )

    # ============================================================
    # 2. User — functional UNIQUE LOWER(email) WHERE deleted_at IS NULL (C-7 3겹 방어)
    # ============================================================
    op.create_table(
        "user",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "onboarding_complete",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "active_day_counter", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("last_active_calendar_date", sa.Date, nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    # functional UNIQUE partial index — schema.md User 인덱스.
    # SQLAlchemy 가 functional index + partial 을 직접 표현하기 어려우므로 raw SQL.
    op.execute(
        'CREATE UNIQUE INDEX ix_user_email ON "user" (LOWER(email)) '
        "WHERE deleted_at IS NULL"
    )

    # ============================================================
    # 3. AdminUser — role CHECK
    # ============================================================
    op.create_table(
        "admin_user",
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="active"
        ),
        sa.Column(
            "must_change_password",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_admin_user_email"),
        sa.CheckConstraint(
            "role IN ('super','operator','read_only')",
            name="ck_admin_user_role",
        ),
    )

    # ============================================================
    # 4. UserConsent — 복합 인덱스 (user_id, consent_type)
    # ============================================================
    op.create_table(
        "user_consent",
        sa.Column("consent_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consent_type", sa.String(40), nullable=False),
        sa.Column("agreed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_user_consent_user_type",
        "user_consent",
        ["user_id", "consent_type"],
    )

    # ============================================================
    # 5. BroadInterest (빈 테이블 — A3 가 12행 시드)
    # ============================================================
    op.create_table(
        "broad_interest",
        sa.Column(
            "broad_interest_id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("cso_cluster_label", sa.String(40), nullable=False),
        sa.Column(
            "cso_seed_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cso_topic.cso_topic_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "display_order", sa.Integer, nullable=False, server_default="0"
        ),
        sa.UniqueConstraint("name", name="uq_broad_interest_name"),
    )

    # ============================================================
    # 6. UserCSOTraversal — A7 가 본문 사용. A2 는 테이블/CHECK/index 만.
    # ============================================================
    op.create_table(
        "user_cso_traversal",
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "path",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_active_day", sa.Integer, nullable=False),
        sa.Column("last_activity_active_day", sa.Integer, nullable=False),
        sa.Column(
            "score_tail", sa.Float, nullable=False, server_default="0.0"
        ),
        sa.Column(
            "created_at",
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
        sa.CheckConstraint(
            "status IN ('active','stale','archived')",
            name="ck_user_cso_traversal_status",
        ),
        # codex C-5: array_length('{}'::uuid[], 1) 는 NULL → CHECK 가 NULL 통과시켜 빈
        # 배열 저장 가능. cardinality(arr) 는 빈 배열에 0 반환 → NULL bypass 차단.
        sa.CheckConstraint(
            "cardinality(path) >= 1",
            name="ck_user_cso_traversal_path_nonempty",
        ),
    )
    op.create_index(
        "ix_user_cso_traversal_user_status",
        "user_cso_traversal",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_user_cso_traversal_path_gin",
        "user_cso_traversal",
        ["path"],
        postgresql_using="gin",
    )

    # ============================================================
    # 7. Source + sentinel 1행 시드
    # ============================================================
    source_table = op.create_table(
        "source",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("trust_level", sa.String(10), nullable=False),
        sa.Column(
            "enabled", sa.Boolean, nullable=False, server_default=sa.true()
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "extra", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.UniqueConstraint("name", name="uq_source_name"),
    )
    # sentinel `cold_start_pseudo` 1행 시드 — cold-start pseudo Document FK 충족용.
    op.bulk_insert(
        source_table,
        [
            {
                "source_id": "00000000-0000-0000-0000-000000000001",
                "name": "cold_start_pseudo",
                "source_type": "vendor_blog",
                "url": "internal://cold-start-pseudo",
                "trust_level": "low",
                "enabled": False,
                "extra": "{}",
            }
        ],
    )

    # ============================================================
    # 8. SourcePolicy + 3행 시드 정확값 (C-8)
    # ============================================================
    source_policy_table = op.create_table(
        "source_policy",
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_category", sa.String(40), nullable=False),
        sa.Column("trust_level", sa.String(10), nullable=False),
        sa.Column(
            "collection_rule",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "enabled", sa.Boolean, nullable=False, server_default=sa.true()
        ),
        sa.UniqueConstraint("source_category", name="uq_source_policy_category"),
    )
    op.bulk_insert(
        source_policy_table,
        [
            {
                "policy_id": "00000000-0000-0000-0000-000000000010",
                "source_category": "academic",
                "trust_level": "high",
                "collection_rule": "{}",
                "enabled": True,
            },
            {
                "policy_id": "00000000-0000-0000-0000-000000000011",
                "source_category": "vendor_blog",
                "trust_level": "high",
                "collection_rule": "{}",
                "enabled": True,
            },
            {
                "policy_id": "00000000-0000-0000-0000-000000000012",
                "source_category": "tech_news",
                "trust_level": "medium",
                "collection_rule": "{}",
                "enabled": True,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("source_policy")
    op.drop_table("source")
    op.drop_index(
        "ix_user_cso_traversal_path_gin", table_name="user_cso_traversal"
    )
    op.drop_index(
        "ix_user_cso_traversal_user_status", table_name="user_cso_traversal"
    )
    op.drop_table("user_cso_traversal")
    op.drop_table("broad_interest")
    op.drop_index("ix_user_consent_user_type", table_name="user_consent")
    op.drop_table("user_consent")
    op.drop_table("admin_user")
    op.drop_index("ix_user_email", table_name="user")
    op.drop_table("user")
    op.drop_index("ix_cso_topic_cluster_labels", table_name="cso_topic")
    op.drop_index("ix_cso_topic_parent", table_name="cso_topic")
    op.drop_index("ix_cso_topic_label", table_name="cso_topic")
    op.drop_table("cso_topic")
