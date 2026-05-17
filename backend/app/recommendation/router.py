"""recommendation router (+ document router) — A8 본문 wire.

두 base path (`/recommendations` + `/documents`) 가 본 모듈 책임. main.py 가 둘 다 include.

docs: api/recommendation.md, algorithms/recommendation-ranking.md, algorithms/cold-start.md.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import User
from app.db.session import get_session
from app.llm_provider import get_provider
from app.llm_provider.protocol import LLMProvider
from app.redis import get_redis
from app.security.deps import require_consent_active
from app.security.rate_limit import limiter

from . import service
from .config_loader import RecommendationConfig, get_recommendation_config
from .schemas import (
    DashboardResponse,
    DocumentDetailResponse,
    DocumentSummaryResponse,
)

recommendation_router = APIRouter(prefix="/recommendations", tags=["recommendation"])
document_router = APIRouter(prefix="/documents", tags=["document"])

_settings = get_settings()


def _redis_default() -> aioredis.Redis:
    return get_redis("default")


def _provider() -> LLMProvider:
    """LLM_PROVIDER env 기반 provider 인스턴스 (stateless — 매 호출 새 build OK)."""
    return get_provider(_settings.LLM_PROVIDER)


def _config() -> RecommendationConfig:
    return get_recommendation_config()


@recommendation_router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="대시보드 10 카드 (UI-02, FR-35~45, NFR-12)",
)
async def get_dashboard_endpoint(
    request: Request,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DashboardResponse:
    """single-flight Redis lock + consent cache. p95 3 초 (concurrency.md §2·§7)."""
    cso_graph = request.app.state.cso_graph
    return await service.get_dashboard(
        db,
        _redis_default(),
        _provider(),
        cso_graph,
        settings,
        _config(),
        user,
    )


@recommendation_router.post(
    "/dashboard/refresh",
    response_model=DashboardResponse,
    summary="캐시 폐기 후 재계산 (1/분/사용자)",
)
@limiter.limit(_settings.RATE_LIMIT_DASHBOARD_REFRESH)
async def refresh_dashboard_endpoint(
    request: Request,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DashboardResponse:
    """rate_limit 1/min/user + cache delete + force_refresh build."""
    cso_graph = request.app.state.cso_graph
    return await service.refresh_dashboard(
        db,
        _redis_default(),
        _provider(),
        cso_graph,
        settings,
        _config(),
        user,
    )


@document_router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="문서 상세 (UI-04)",
)
async def get_document_detail_endpoint(
    request: Request,
    document_id: UUID,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentDetailResponse:
    """Document 메타 + saved/hidden flag + related TopicChip."""
    return await service.get_document_detail(db, user, document_id)


@document_router.get(
    "/{document_id}/summary",
    response_model=DocumentSummaryResponse,
    summary="섹션형 LLM 요약 (FR-51)",
)
async def get_document_summary_endpoint(
    request: Request,
    document_id: UUID,
    user: Annotated[User, Depends(require_consent_active)],
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentSummaryResponse:
    """DocumentSummaryCache hit 시 즉시 / miss 시 LLM medium + INSERT.

    LLM 실패 → generator='source_abstract' fallback (또는 503 if no summary).
    """
    return await service.get_document_summary(
        db,
        _redis_default(),
        _provider(),
        settings,
        document_id,
    )


__all__ = ["document_router", "recommendation_router"]
