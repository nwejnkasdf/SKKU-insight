"""Document ORM — schema.md Document §, alembic 0003 document 테이블.

v13 라운드 (2026-05-11) A4 Topic-driven Pivot 결과로 도입된 영속 모델.
- `source_id` 는 1차 시연에서 sentinel `llm_search` (alembic 0003 시드) 단일.
- `summary` 는 NFR-25 정합 — LLM self-summary (외부 원문 복사 금지) 본인 말 요약.
- `raw` JSONB 에 publisher 정보 (publisher_domain, publisher_label, trust_hint, llm_meta).

CHECK: content_type IN ('academic_paper','vendor_blog','tech_news','pseudo_cold_start')
       — contracts.ContentType enum.
인덱스:
- ix_document_source (source_id) — sentinel 기준 필터
- ix_document_normalized_title — 중복 후보 lookup
- ix_document_published_at — /topics/{id}/documents 시간순 정렬
- ix_document_doi — dedup DOI lookup
- 부분 UNIQUE 인덱스 (canonical_url / doi WHERE NOT NULL) 는 alembic raw SQL.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Document(Base):
    """수집 문서 메타. v13 pivot 후 모든 row 의 source_id = sentinel `llm_search`."""

    __tablename__ = "document"
    __table_args__ = (
        CheckConstraint(
            "content_type IN ('academic_paper','vendor_blog','tech_news','pseudo_cold_start')",
            name="ck_document_content_type",
        ),
        Index("ix_document_source", "source_id"),
        Index("ix_document_normalized_title", "normalized_title"),
        Index("ix_document_published_at", "published_at"),
        Index("ix_document_doi", "doi"),
        # (Codex round 2 S-07) alembic 0003 의 partial UNIQUE INDEX 를 ORM 메타에 미러링
        # → `alembic check` autogenerate diff = 0 보장. cross-user dedup (C-03) 의 핵심 키.
        Index(
            "ux_document_canonical_url",
            "canonical_url",
            unique=True,
            postgresql_where=text("canonical_url IS NOT NULL"),
        ),
        Index(
            "ux_document_doi_partial",
            "doi",
            unique=True,
            postgresql_where=text("doi IS NOT NULL"),
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source.source_id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["Document"]
