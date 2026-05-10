"""auth Pydantic schemas — docs/api/auth.md.

email 정규화 3겹 방어 (auth-flow.md §결정 핀): Pydantic validator 가 첫 겹.
SignupRequest / LoginRequest 의 email 은 lowercase + trim 후 EmailStr 검증.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


def _normalize_email(v: str) -> str:
    """lowercase + trim. EmailStr 검증 전 단계."""
    if not isinstance(v, str):
        return v
    return v.strip().lower()


class SignupRequest(BaseModel):
    """회원가입 요청. email 정규화 + password 정책 검증 (service 단)."""

    email: EmailStr
    password: str

    _normalize_email = field_validator("email", mode="before")(_normalize_email)


class SignupResponse(BaseModel):
    """회원가입 결과. 신규 가입은 항상 onboarding_required=true."""

    user_id: UUID
    email: EmailStr
    onboarding_required: bool
    consent_required: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    _normalize_email = field_validator("email", mode="before")(_normalize_email)


class TokenPair(BaseModel):
    """JWT access + refresh 쌍. token_type 은 Bearer 고정."""

    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    """로그아웃 시 refresh token 함께 폐기 (codex C-2). access jti 는 헤더의 Bearer 에서 추출."""

    refresh_token: str | None = None


class MeResponse(BaseModel):
    """`GET /auth/me` 응답."""

    user_id: UUID
    email: EmailStr
    created_at: datetime
    consent_active: bool
    onboarding_complete: bool
