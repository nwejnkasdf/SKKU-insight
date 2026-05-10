"""auth Pydantic schemas — docs/api/auth.md.

Phase 0a stub: schema 정의는 정확히 (OpenAPI export 가능). validator 는 Phase 0b 가 추가
(예: password-policy.md 룰).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr


class SignupRequest(BaseModel):
    """회원가입 요청. password 정책 검증은 Phase 0b 가 password-policy.md 룰로."""

    email: EmailStr
    password: str


class SignupResponse(BaseModel):
    """회원가입 결과. 신규 가입은 항상 onboarding_required=true."""

    user_id: UUID
    email: EmailStr
    onboarding_required: bool
    consent_required: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    """JWT access + refresh 쌍. token_type 은 Bearer 고정."""

    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    """`GET /auth/me` 응답."""

    user_id: UUID
    email: EmailStr
    created_at: datetime
    consent_active: bool
    onboarding_complete: bool
