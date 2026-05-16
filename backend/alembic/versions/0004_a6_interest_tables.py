"""A6 0004: UserEvent + UserInterestState + SavedDocument + HiddenDocument + NotInterestedTopic + SystemConfig.

Revision ID: 0004_a6_interest_tables
Revises: 0003_a4_collection_tables
Create Date: 2026-05-17

A6 (interest-bayesian) phase 1. 6 신규 테이블 + 12 partial UNIQUE index + system_config seed.

테이블:
- user_event: 행동 로그 (NotImplementedError stub 본문 → A6 PR-3 ingest_event_atomic).
  payload_hash 컬럼 신규 (idempotency 200/409 분기용).
- user_interest_state: Beta-Bernoulli 사후 (long_alpha/beta, short_alpha/beta).
  boost_applied_at_active_day 컬럼 신규 (14-day boost 만료 cron 추적용).
  partial UNIQUE 3종 (cso_only / leaf_only / pair).
- saved_document: SavedDocument composite PK.
- hidden_document: HiddenDocument composite PK.
- not_interested_topic: 명시 토픽 거부 마킹. partial UNIQUE 3종.
- system_config: interest_params + event_weights JSONB seed 2 row. A6 lifespan startup read,
  A10 admin-console 가 PUT /admin/system-config 로 변경.

JSONB seed 는 dict 형식 (codex A2 C-26 lesson — str "{}" 금지).

forward-only 정책 (downgrade 는 테스트 정합용만).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_a6_interest_tables"
down_revision: str | None = "0003_a4_collection_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ============================================================
    # 1. user_event — 행동 로그 audit + idempotency
    # ============================================================
    op.create_table(
        "user_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.document_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "cso_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "leaf_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("dwell_ms", sa.Integer, nullable=True),
        sa.Column("client_request_id", sa.String(120), nullable=False),
        # payload_hash 는 sha256[:32] 64 hex char. idempotency 200/409 분기.
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('view','click','dwell_tick','open_external','save','hide','not_interested')",
            name="ck_user_event_type",
        ),
        sa.UniqueConstraint(
            "user_id", "client_request_id", name="uq_user_event_idempotency"
        ),
    )
    op.create_index(
        "ix_user_event_user_created",
        "user_event",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_user_event_type", "user_event", ["event_type"])

    # ============================================================
    # 2. user_interest_state — Beta-Bernoulli 사후 (단·장기) + active day 추적
    # ============================================================
    op.create_table(
        "user_interest_state",
        sa.Column("state_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cso_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "leaf_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("long_alpha", sa.Float, nullable=False),
        sa.Column("long_beta", sa.Float, nullable=False),
        sa.Column("short_alpha", sa.Float, nullable=False),
        sa.Column("short_beta", sa.Float, nullable=False),
        sa.Column("long_score", sa.Float, nullable=False),
        sa.Column("short_score", sa.Float, nullable=False),
        sa.Column(
            "last_event_active_day",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "last_decay_active_day",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        # 14-day onboarding boost 만료 cron 추적. NULL = boost 미적용 row.
        sa.Column(
            "boost_applied_at_active_day", sa.Integer, nullable=True
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL",
            name="ck_user_interest_state_at_least_one_topic",
        ),
    )
    op.create_index(
        "ix_user_interest_state_user", "user_interest_state", ["user_id"]
    )
    # partial UNIQUE 3종 — schema.md §UserInterestState 룰 정합.
    op.execute(
        "CREATE UNIQUE INDEX ux_user_interest_state_cso_only "
        "ON user_interest_state (user_id, cso_topic_id) "
        "WHERE leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_user_interest_state_leaf_only "
        "ON user_interest_state (user_id, leaf_topic_id) "
        "WHERE cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_user_interest_state_pair "
        "ON user_interest_state (user_id, cso_topic_id, leaf_topic_id) "
        "WHERE cso_topic_id IS NOT NULL AND leaf_topic_id IS NOT NULL"
    )

    # ============================================================
    # 3. saved_document — UI-05 저장. composite PK.
    # ============================================================
    op.create_table(
        "saved_document",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_saved_document_user_saved",
        "saved_document",
        ["user_id", sa.text("saved_at DESC")],
    )

    # ============================================================
    # 4. hidden_document — 숨김. composite PK.
    # ============================================================
    op.create_table(
        "hidden_document",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "hidden_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_hidden_document_user_hidden",
        "hidden_document",
        ["user_id", sa.text("hidden_at DESC")],
    )

    # ============================================================
    # 5. not_interested_topic — 명시 토픽 거부 마킹. partial UNIQUE 3종.
    # ============================================================
    op.create_table(
        "not_interested_topic",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cso_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cso_topic.cso_topic_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "leaf_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL",
            name="ck_not_interested_topic_at_least_one_topic",
        ),
    )
    op.create_index(
        "ix_not_interested_topic_user", "not_interested_topic", ["user_id"]
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_not_interested_topic_cso_only "
        "ON not_interested_topic (user_id, cso_topic_id) "
        "WHERE leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_not_interested_topic_leaf_only "
        "ON not_interested_topic (user_id, leaf_topic_id) "
        "WHERE cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_not_interested_topic_pair "
        "ON not_interested_topic (user_id, cso_topic_id, leaf_topic_id) "
        "WHERE cso_topic_id IS NOT NULL AND leaf_topic_id IS NOT NULL"
    )

    # ============================================================
    # 6. system_config — interest_params + event_weights JSONB SOR.
    #    A6 lifespan read-only, A10 admin-console 가 PUT 으로 변경.
    # ============================================================
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_by_admin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_user.admin_id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ============================================================
    # 7. system_config seed — interest_params + event_weights 초기값.
    #    algorithms/interest-bayesian.md §구성 파일 스키마 그대로.
    #    JSONB 는 dict (codex A2 C-26 — str "{}" 금지).
    # ============================================================
    system_config_table = sa.table(
        "system_config",
        sa.column("key", sa.String),
        sa.column("value", postgresql.JSONB),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        system_config_table,
        [
            {
                "key": "interest_params",
                "value": {
                    "alpha_prior": 1.0,
                    "beta_prior": 4.0,
                    "half_life_short_active_days": 7,
                    "half_life_long_active_days": 60,
                    "onboarding_prior_boost": 1.0,
                    "onboarding_boost_active_days": 14,
                    "propagation_hop_decay": 0.5,
                    "propagation_max_hops": 4,
                    "propagation_non_trace_ancestors": False,
                    "bucket_high_long": 0.70,
                    "bucket_high_short": 0.60,
                    "bucket_medium": 0.50,
                    "bucket_low": 0.30,
                },
                "description": (
                    "A6 Beta-Bernoulli prior, decay half-life, propagation, "
                    "bucket thresholds. interest-bayesian.md §구성 파일 스키마."
                ),
            },
            {
                "key": "event_weights",
                "value": {
                    "weights": {
                        "view": 0.0,
                        "click": 1.0,
                        "dwell_tick": 0.5,
                        "open_external": 2.0,
                        "save": 5.0,
                        "hide": -3.0,
                        "not_interested": -5.0,
                    },
                    "caps": {
                        "dwell_tick_max_per_document": 4,
                        "weight_per_event_max": 5.0,
                    },
                },
                "description": (
                    "A6 event-to-likelihood mapping + caps. dwell_tick cap 4회 "
                    "(30s×4=2분, SRS 체류 ≥2m 정렬)."
                ),
            },
        ],
    )


def downgrade() -> None:
    # forward-only 정책 — 본 함수는 테스트 정합용만.
    op.execute("DELETE FROM system_config WHERE key IN ('interest_params','event_weights')")
    op.drop_table("system_config")
    op.execute("DROP INDEX IF EXISTS ux_not_interested_topic_pair")
    op.execute("DROP INDEX IF EXISTS ux_not_interested_topic_leaf_only")
    op.execute("DROP INDEX IF EXISTS ux_not_interested_topic_cso_only")
    op.drop_index(
        "ix_not_interested_topic_user", table_name="not_interested_topic"
    )
    op.drop_table("not_interested_topic")
    op.drop_index(
        "ix_hidden_document_user_hidden", table_name="hidden_document"
    )
    op.drop_table("hidden_document")
    op.drop_index(
        "ix_saved_document_user_saved", table_name="saved_document"
    )
    op.drop_table("saved_document")
    op.execute("DROP INDEX IF EXISTS ux_user_interest_state_pair")
    op.execute("DROP INDEX IF EXISTS ux_user_interest_state_leaf_only")
    op.execute("DROP INDEX IF EXISTS ux_user_interest_state_cso_only")
    op.drop_index(
        "ix_user_interest_state_user", table_name="user_interest_state"
    )
    op.drop_table("user_interest_state")
    op.drop_index("ix_user_event_type", table_name="user_event")
    op.drop_index("ix_user_event_user_created", table_name="user_event")
    op.drop_table("user_event")
