"""consent router — Phase 0a stub.

docs: api/consent.md, security/auth-flow.md.
"""
from __future__ import annotations

from fastapi import APIRouter, status

from .schemas import (
    AccountDeletionRequest,
    AccountDeletionResponse,
    ConsentRequest,
    ConsentRevokeRequest,
    ConsentStateResponse,
)

router = APIRouter(prefix="/consent", tags=["consent"])


@router.get(
    "",
    response_model=ConsentStateResponse,
    summary="자기 동의 상태 조회 (FR-05·06)",
)
async def get_consent_state() -> ConsentStateResponse:
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ConsentStateResponse,
    summary="동의 등록·갱신 (FR-05·11)",
)
async def post_consent(req: ConsentRequest) -> ConsentStateResponse:
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.post(
    "/revoke",
    response_model=ConsentStateResponse,
    summary="동의 철회 (FR-58·59)",
)
async def revoke_consent(req: ConsentRevokeRequest) -> ConsentStateResponse:
    """철회 후 추천 캐시 폐기 + 보호 API 호출 차단 (FR-59)."""
    raise NotImplementedError("Phase 0b A2에서 구현")


@router.post(
    "/account-deletion",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AccountDeletionResponse,
    summary="계정·개인화 데이터 삭제 (FR-56)",
)
async def request_account_deletion(req: AccountDeletionRequest) -> AccountDeletionResponse:
    """NFR-21. 1차 시연은 즉시 cascade — decision-backlog C-2."""
    raise NotImplementedError("Phase 0b A2에서 구현")
