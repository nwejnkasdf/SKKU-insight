"""recommendation Pydantic schemas — docs/api/recommendation.md."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.contracts import SlotType, SourceType, TopicChip


class RecommendationCard(BaseModel):
    """추천 카드 1 개. 점수는 NFR-04 따라 노출 X."""

    recommendation_id: UUID
    document_id: UUID
    slot_type: SlotType
    title: str
    source_name: str
    source_type: SourceType
    related_topics: list[TopicChip]
    reason_short: str  # 한국어 1 문장 (NFR-03)
    published_at: datetime
    thumbnail_url: str | None = None


class SlotSummary(BaseModel):
    """슬롯별 채움 상태 (FR-37·42·43)."""

    slot_type: SlotType
    target_count: int
    actual_count: int
    fallback_reason: str | None = None


class DashboardResponse(BaseModel):
    """`GET /recommendations/dashboard` — UI-02. 항상 10 카드."""

    user_id: UUID
    cards: list[RecommendationCard]
    slots: list[SlotSummary]
    generated_at: datetime
    cache: Literal["hit", "miss"]
    cold_start: bool


class DocumentDetailResponse(BaseModel):
    """`GET /documents/{id}` — UI-04."""

    document_id: UUID
    title: str
    source_name: str
    source_type: SourceType
    url: str
    canonical_url: str | None = None
    published_at: datetime
    summary_short: str
    related_topics: list[TopicChip]
    saved: bool
    hidden: bool


class DocumentSummarySection(BaseModel):
    """섹션형 LLM 요약 1 단락 (FR-51)."""

    section: Literal["core", "background", "significance", "limitations"]
    title_ko: str
    body_ko: str


class DocumentSummaryResponse(BaseModel):
    """`GET /documents/{id}/summary`."""

    document_id: UUID
    sections: list[DocumentSummarySection]
    generator: Literal["llm", "source_abstract"]
    generated_at: datetime
    reason_short: str
