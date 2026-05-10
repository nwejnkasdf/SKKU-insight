"""recommendation router (+ document router) — Phase 0a stub.

두 base path (`/recommendations` + `/documents`) 가 본 모듈 책임. main.py 가 둘 다 include.

docs: api/recommendation.md, algorithms/recommendation-ranking.md, algorithms/cold-start.md.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from .schemas import (
    DashboardResponse,
    DocumentDetailResponse,
    DocumentSummaryResponse,
)

recommendation_router = APIRouter(prefix="/recommendations", tags=["recommendation"])
document_router = APIRouter(prefix="/documents", tags=["document"])


@recommendation_router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="대시보드 10 카드 (UI-02, FR-35~45, NFR-12)",
)
async def get_dashboard() -> DashboardResponse:
    """single-flight Redis lock + consent cache. p95 3 초 (concurrency.md §2·§7)."""
    raise NotImplementedError("Phase 0b A8에서 구현")


@recommendation_router.post(
    "/dashboard/refresh",
    response_model=DashboardResponse,
    summary="캐시 폐기 후 재계산 (1/분/사용자)",
)
async def refresh_dashboard() -> DashboardResponse:
    raise NotImplementedError("Phase 0b A8에서 구현")


@document_router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="문서 상세 (UI-04)",
)
async def get_document_detail(document_id: UUID) -> DocumentDetailResponse:
    raise NotImplementedError("Phase 0b A8에서 구현")


@document_router.get(
    "/{document_id}/summary",
    response_model=DocumentSummaryResponse,
    summary="섹션형 LLM 요약 (FR-51)",
)
async def get_document_summary(document_id: UUID) -> DocumentSummaryResponse:
    """LLM 실패 시 generator='source_abstract' fallback (503 + document.summary_unavailable)."""
    raise NotImplementedError("Phase 0b A8에서 구현")
