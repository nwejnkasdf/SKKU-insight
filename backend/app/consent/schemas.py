"""consent Pydantic schemas — docs/api/consent.md."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# 1차는 단일 타입. EV 시 마케팅 등 추가.
ConsentType = Literal["personalization"]


class ConsentRequest(BaseModel):
    """동의 등록·갱신. agreed=false 는 거부 — 철회는 /revoke 로."""

    consent_type: ConsentType
    agreed: bool


class ConsentRecord(BaseModel):
    consent_id: UUID
    consent_type: ConsentType
    agreed_at: datetime
    revoked_at: datetime | None = None


class ConsentStateResponse(BaseModel):
    """`GET /consent` — personalization 동의 활성 여부 + 이력."""

    user_id: UUID
    records: list[ConsentRecord]
    active: bool
    onboarding_required: bool


class ConsentRevokeRequest(BaseModel):
    """철회 요청. confirmation 필드는 사용자 의도 명시 안전장치."""

    consent_type: ConsentType
    confirmation: Literal["confirm"]


class AccountDeletionRequest(BaseModel):
    """계정·개인화 데이터 삭제 요청. NFR-21 30 일 grace 는 1차 미구현 (decision-backlog C-2)."""

    reason: str | None = None
    confirmation: Literal["confirm"]


class AccountDeletionResponse(BaseModel):
    request_id: UUID
    status: Literal["queued"] = "queued"
    expected_deletion_by: datetime
