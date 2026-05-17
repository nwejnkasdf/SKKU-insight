"""DocumentSummaryCache ORM — schema.md DocumentSummaryCache §, alembic 0006.

A8 (recommendation engine). FR-51 섹션형 LLM 요약 캐시 (Document 1:1).

sections JSONB = list[{section, title_ko, body_ko}] — recommendation/schemas.py
`DocumentSummarySection` 4종 ('core' | 'background' | 'significance' | 'limitations').

generator CHECK ('llm' | 'source_abstract'):
- 'llm': model_slot=medium 호출 성공.
- 'source_abstract': LLM 실패 → Document.summary[:500] 1 섹션 fallback.

PK = document_id (1:1) — 같은 문서 중복 캐시 차단. 동시 INSERT race 는
`pg_insert(...).on_conflict_do_nothing(index_elements=["document_id"])` 패턴
(A6 C-03 lesson — IntegrityError 회피 + caller None-check + lookup fallback).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentSummaryCache(Base):
    """FR-51 섹션형 LLM 요약 1:1 캐시 (Document.document_id PK + FK CASCADE)."""

    __tablename__ = "document_summary_cache"
    __table_args__ = (
        CheckConstraint(
            "generator IN ('llm','source_abstract')",
            name="ck_document_summary_cache_generator",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    reason_short: Mapped[str] = mapped_column(String(255), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    model_used: Mapped[str] = mapped_column(String(120), nullable=False)
    generator: Mapped[str] = mapped_column(String(20), nullable=False)


__all__ = ["DocumentSummaryCache"]
