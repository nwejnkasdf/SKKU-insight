"""onboarding router — Phase 0a stub.

docs: api/onboarding.md, algorithms/cold-start.md, algorithms/cso-topic-traversal.md §7.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from .schemas import (
    ColdStartStatusResponse,
    OnboardingInterestsRequest,
    OnboardingInterestsResponse,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post(
    "/interests",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OnboardingInterestsResponse,
    summary="클러스터 선택 + cold-start 트리거 (FR-07~10)",
)
async def post_onboarding_interests(
    req: OnboardingInterestsRequest,
) -> OnboardingInterestsResponse:
    """Single-flight Redis lock (lock:onboarding:{user_id}). UserConsent 활성 검증."""
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.get(
    "/cold-start-status/{request_id}",
    response_model=ColdStartStatusResponse,
    summary="cold-start LLM 진행 폴링",
)
async def get_cold_start_status(request_id: UUID) -> ColdStartStatusResponse:
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.put(
    "/interests",
    response_model=OnboardingInterestsResponse,
    summary="설정 화면 — 관심 분야 수정 (FR-55)",
)
async def put_onboarding_interests(
    req: OnboardingInterestsRequest,
) -> OnboardingInterestsResponse:
    """추가 cluster = prior boost 추가. 제거 cluster = 그 root 의 active trace stale."""
    raise NotImplementedError("Phase 0b A2에서 구현")
