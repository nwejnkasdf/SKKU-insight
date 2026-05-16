"""A4 0003: Document + DocumentTopic + CollectionJob + ClickbaitResult 신규 + llm_search sentinel.

Revision ID: 0003_a4_collection_tables
Revises: 0002_a3_cso_traversal_leaf
Create Date: 2026-05-16

v13 라운드 (2026-05-11) A4 Topic-driven Pivot 의 schema 단. 4 테이블:
- document: 수집 문서 메타. summary = NFR-25 LLM self-summary. raw JSONB = publisher 정보.
- document_topic: Document ↔ (cso_topic|leaf_topic) M:N. 부분 UNIQUE 3종.
- collection_job: 1 user/run = 1 row (Q4 결정). target_*=NULL. failure_reason = partial summary.
- clickbait_result: 1차 비활성 (CLICKBAIT_ENABLED=false). schema 보존만.

신규 sentinel: `llm_search` 1행 (source_type=vendor_blog, trust_level=high, enabled=true).
- cold_start_pseudo 는 0001 에서 시드됨 (추가 X).
- 고정 UUID `00000000-0000-0000-0000-000000000002` (코드 lookup 용).

JSONB seed dict 형식 (codex C-25 — str "{}" 금지).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_a4_collection_tables"
down_revision: str | None = "0002_a3_cso_traversal_leaf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ============================================================
    # 1. Document — content_type CHECK 4종, partial UNIQUE 2종 (canonical_url, doi)
    # ============================================================
    op.create_table(
        "document",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source.source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("normalized_title", sa.Text, nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("canonical_url", sa.String(1000), nullable=True),
        sa.Column("doi", sa.String(120), nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_type", sa.String(40), nullable=False),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column(
            "raw", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_type IN ('academic_paper','vendor_blog','tech_news','pseudo_cold_start')",
            name="ck_document_content_type",
        ),
    )
    op.create_index("ix_document_source", "document", ["source_id"])
    op.create_index(
        "ix_document_normalized_title", "document", ["normalized_title"]
    )
    op.create_index("ix_document_published_at", "document", ["published_at"])
    op.create_index("ix_document_doi", "document", ["doi"])
    # 부분 UNIQUE 인덱스 — canonical_url/doi NOT NULL 인 경우만 유일성 강제.
    op.execute(
        "CREATE UNIQUE INDEX ux_document_canonical_url "
        "ON document (canonical_url) WHERE canonical_url IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_document_doi_partial "
        "ON document (doi) WHERE doi IS NOT NULL"
    )

    # ============================================================
    # 2. DocumentTopic — Document ↔ (cso_topic|leaf_topic) M:N. 부분 UNIQUE 3종.
    # ============================================================
    op.create_table(
        "document_topic",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.document_id", ondelete="CASCADE"),
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
        sa.Column("confidence", sa.Float, nullable=False),
        sa.CheckConstraint(
            "cso_topic_id IS NOT NULL OR leaf_topic_id IS NOT NULL",
            name="ck_document_topic_at_least_one_topic",
        ),
    )
    op.create_index("ix_document_topic_document", "document_topic", ["document_id"])
    op.create_index("ix_document_topic_cso", "document_topic", ["cso_topic_id"])
    op.create_index("ix_document_topic_leaf", "document_topic", ["leaf_topic_id"])
    # 부분 UNIQUE 3종 — schema.md §DocumentTopic 룰 정합.
    op.execute(
        "CREATE UNIQUE INDEX ux_document_topic_cso_only "
        "ON document_topic (document_id, cso_topic_id) "
        "WHERE leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_document_topic_leaf_only "
        "ON document_topic (document_id, leaf_topic_id) "
        "WHERE cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_document_topic_pair "
        "ON document_topic (document_id, cso_topic_id, leaf_topic_id) "
        "WHERE cso_topic_id IS NOT NULL AND leaf_topic_id IS NOT NULL"
    )

    # ============================================================
    # 3. CollectionJob — 1 user/run = 1 row (Q4). 2 composite index.
    # ============================================================
    op.create_table(
        "collection_job",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.user_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source.source_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_cso_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cso_topic.cso_topic_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_leaf_topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dynamic_leaf_topic.leaf_topic_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column(
            "retry_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "job_type IN ('daily_collect','leaf_lifecycle','merge_evaluation','summary_generation')",
            name="ck_collection_job_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','skipped')",
            name="ck_collection_job_status",
        ),
    )
    op.create_index(
        "ix_collection_job_status_finished",
        "collection_job",
        ["status", "finished_at"],
    )
    op.create_index(
        "ix_collection_job_user_finished",
        "collection_job",
        ["user_id", "finished_at"],
    )

    # ============================================================
    # 4. ClickbaitResult — 1차 비활성. document_id UNIQUE.
    # ============================================================
    op.create_table(
        "clickbait_result",
        sa.Column("result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("adapter_type", sa.String(20), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", name="uq_clickbait_result_document"),
        sa.CheckConstraint(
            "decision IN ('clickbait','clean','error')",
            name="ck_clickbait_result_decision",
        ),
    )

    # ============================================================
    # 5. Source sentinel `llm_search` 1행 시드 (v13 pivot).
    # ============================================================
    source_table = sa.table(
        "source",
        sa.column("source_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("source_type", sa.String),
        sa.column("url", sa.String),
        sa.column("trust_level", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("extra", postgresql.JSONB),
    )
    op.bulk_insert(
        source_table,
        [
            {
                "source_id": "00000000-0000-0000-0000-000000000002",
                "name": "llm_search",
                "source_type": "vendor_blog",
                "url": "internal://llm-search",
                "trust_level": "high",
                "enabled": True,
                "extra": {"sentinel": True, "description": "v13 pivot LLM 결과"},
            }
        ],
    )


def downgrade() -> None:
    # sentinel 삭제 (forward-only 정책이지만 downgrade 테스트 정합 위해).
    op.execute(
        "DELETE FROM source WHERE source_id = '00000000-0000-0000-0000-000000000002'"
    )
    op.drop_table("clickbait_result")
    op.drop_index(
        "ix_collection_job_user_finished", table_name="collection_job"
    )
    op.drop_index(
        "ix_collection_job_status_finished", table_name="collection_job"
    )
    op.drop_table("collection_job")
    op.execute("DROP INDEX IF EXISTS ux_document_topic_pair")
    op.execute("DROP INDEX IF EXISTS ux_document_topic_leaf_only")
    op.execute("DROP INDEX IF EXISTS ux_document_topic_cso_only")
    op.drop_index("ix_document_topic_leaf", table_name="document_topic")
    op.drop_index("ix_document_topic_cso", table_name="document_topic")
    op.drop_index("ix_document_topic_document", table_name="document_topic")
    op.drop_table("document_topic")
    op.execute("DROP INDEX IF EXISTS ux_document_doi_partial")
    op.execute("DROP INDEX IF EXISTS ux_document_canonical_url")
    op.drop_index("ix_document_doi", table_name="document")
    op.drop_index("ix_document_published_at", table_name="document")
    op.drop_index("ix_document_normalized_title", table_name="document")
    op.drop_index("ix_document_source", table_name="document")
    op.drop_table("document")
