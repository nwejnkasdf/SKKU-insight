"""onboarding router — Phase 0b A2 본문.

POST /interests + GET /cold-start-status/{request_id} + PUT /interests.
docs: api/onboarding.md, algorithms/cold-start.md.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.session import get_session
from app.redis import get_redis
from app.security.deps import get_current_user
from app.security.idempotency import get_idempotency_key
from app.security.rate_limit import limiter

from . import service
from .schemas import (
    ColdStartStatusResponse,
    OnboardingInterestsRequest,
    OnboardingInterestsResponse,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def _redis_default() -> aioredis.Redis:
    return get_redis("default")


_settings = get_settings()


def _is_prefer_sync(prefer_header: str | None) -> bool:
    """`Prefer: respond=sync` 헤더 파싱 (api-conventions.md §11)."""
    if not prefer_header:
        return False
    # `respond=sync` 또는 `respond-async` (async 우선시) 등 다양한 표기 대응
    tokens = [t.strip().lower() for t in prefer_header.split(",")]
    return "respond=sync" in tokens or "wait=8" in tokens


@router.post(
    "/interests",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OnboardingInterestsResponse,
    summary="클러스터 선택 + cold-start 트리거 (FR-07~10)",
)
@limiter.limit(_settings.RATE_LIMIT_ONBOARDING)
async def post_onboarding_interests_endpoint(
    request: Request,
    req: OnboardingInterestsRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Depends(get_idempotency_key)],
    prefer: Annotated[str | None, Header(alias="Prefer")] = None,
) -> OnboardingInterestsResponse:
    """Single-flight Redis lock + consent active 검증 + RQ enqueue."""
    return await service.post_interests(
        user,
        req,
        request=request,
        db=db,
        redis=_redis_default(),
        idempotency_key=idempotency_key,
        prefer_sync=_is_prefer_sync(prefer),
    )


@router.get(
    "/cold-start-status/{request_id}",
    response_model=ColdStartStatusResponse,
    summary="cold-start LLM 진행 폴링",
)
@limiter.limit(_settings.RATE_LIMIT_DEFAULT)
async def get_cold_start_status_endpoint(
    request: Request,
    request_id: UUID,
) -> ColdStartStatusResponse:
    return await service.get_cold_start_status(
        request_id, request=request, redis=_redis_default()
    )


@router.put(
    "/interests",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OnboardingInterestsResponse,
    summary="설정 화면 — 관심 분야 수정 (FR-55)",
)
@limiter.limit(_settings.RATE_LIMIT_ONBOARDING_UPDATE)
async def put_onboarding_interests_endpoint(
    request: Request,
    req: OnboardingInterestsRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> OnboardingInterestsResponse:
    """1차 시연: cluster 검증 + 202. prior boost 는 A6, stale 마킹은 A7."""
    return await service.put_interests(
        user, req, request=request, db=db, redis=_redis_default()
    )
