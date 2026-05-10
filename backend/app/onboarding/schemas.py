"""onboarding Pydantic schemas — docs/api/onboarding.md.

12 CSO 클러스터 조회는 topics 모듈의 `GET /topics/cso/clusters` 단일 endpoint 를 사용.
본 모듈은 사용자가 cluster 를 선택해 cold-start 를 트리거하는 흐름만.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.contracts import UserClass


class OnboardingInterestsRequest(BaseModel):
    """클러스터 선택 + cold-start 트리거.

    user_class 는 transient — User 영구 저장 안 함 (decision-backlog P1-1).
    """

    cso_cluster_ids: list[UUID]
    user_class: UserClass = UserClass.GENERAL
    locale: Literal["ko", "en"] = "ko"


class OnboardingInterestsResponse(BaseModel):
    """비동기 cold-start. polling_url 로 GET /onboarding/cold-start-status."""

    request_id: UUID
    status: Literal["queued", "completed"]
    polling_url: str
    estimated_seconds: int


class ColdStartStatusResponse(BaseModel):
    """cold-start LLM 진행 상태 폴링 응답."""

    request_id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    progress_percent: int
    completed_at: datetime | None = None
    dashboard_ready: bool
    error_code: str | None = None
