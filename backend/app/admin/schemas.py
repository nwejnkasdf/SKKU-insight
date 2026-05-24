"""admin Pydantic schemas — docs/api/admin.md. (관리자 콘솔 전용)

본 모듈은 collection.md 의 관리자 영역 schema (ReprocessRequestView, SourceView 등) 도
포함한다. 사용자 결정(2026-05-11): `/admin/collection/*` endpoint 와 그 schema 의
단일 SOR 은 admin 모듈.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.collection.schemas import CollectionJobView
from app.contracts import (
    AdminRole,
    ClickbaitDecision,
    CollectionJobStatus,
    EventType,
    InterestBucket,
    SourceType,
    TrustLevel,
)

# ============================================================
# 인증
# ============================================================


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AdminTokenPair(BaseModel):
    """관리자 JWT 쌍. 부트스트랩 직후 must_change_password=true."""

    access_token: str
    refresh_token: str
    expires_in: int
    must_change_password: bool


class AdminRefreshRequest(BaseModel):
    refresh_token: str


class AdminLogoutRequest(BaseModel):
    """로그아웃 시 admin refresh token 함께 폐기 (codex C-2). access jti 는 Bearer 에서 추출."""

    refresh_token: str | None = None


class AdminMeResponse(BaseModel):
    """admin 자기 정보 (admin.md schema 정의됨, endpoint 는 Phase 0b 에서 추가 검토)."""

    admin_id: UUID
    email: EmailStr
    role: AdminRole
    status: Literal["active", "suspended"]
    last_login_at: datetime | None = None


class ChangeAdminPasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ============================================================
# 수집 (collection.md 관리자 영역)
# ============================================================


class ReprocessRequestPayload(BaseModel):
    reason: str | None = None


class ReprocessRequestView(BaseModel):
    request_id: UUID
    admin_id: UUID
    job_id: UUID
    requested_at: datetime
    status: Literal["queued", "running", "succeeded", "failed"]
    result_message: str | None = None


class SourceView(BaseModel):
    source_id: UUID
    name: str
    source_type: SourceType
    url: str
    trust_level: TrustLevel
    enabled: bool
    last_success_at: datetime | None = None


class SourceTogglePatch(BaseModel):
    """`PATCH /admin/collection/sources/{id}` — 활성/비활성 토글."""

    enabled: bool


class CollectionStatsResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    success_rate: float
    total_jobs: int
    failed_jobs: int
    failures_by_source: dict[str, int]
    alert: Literal["below_sla"] | None = None


# ============================================================
# 낚시성 통계 (FR-33·63)
# ============================================================


class ClickbaitBySource(BaseModel):
    source_name: str
    evaluated: int
    clickbait: int


class ClickbaitStatsResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    total_evaluated: int
    clickbait_count: int
    clean_count: int
    excluded_per_user_avg: float
    by_source: dict[str, ClickbaitBySource]


class ClickbaitResultView(BaseModel):
    result_id: UUID
    document_id: UUID
    document_title: str
    model_name: str
    adapter_type: Literal["dora"]
    decision: ClickbaitDecision
    confidence: float
    evaluated_at: datetime


# ============================================================
# 토픽 연결 오류 (FR-64)
# ============================================================


class TopicLinkageErrorView(BaseModel):
    error_id: UUID
    document_id: UUID
    expected_cso_topic_id: UUID | None = None
    error_message: str
    retry_count: int
    occurred_at: datetime


# ============================================================
# 사용자 (NFR-04 우회 — 관리자만 점수 열람)
# ============================================================


class AdminUserListItem(BaseModel):
    user_id: UUID
    email: EmailStr  # role 별 마스킹은 응답 직렬화 시점 처리
    created_at: datetime
    consent_active: bool
    deletion_pending: bool
    latest_collection_status: CollectionJobStatus | None = None
    latest_collection_created_at: datetime | None = None
    latest_collection_started_at: datetime | None = None
    latest_collection_finished_at: datetime | None = None


class AdminInterestTopicView(BaseModel):
    """관리자 콘솔에서 점수 그대로 노출."""

    cso_topic_id: UUID | None = None
    leaf_topic_id: UUID | None = None
    label: str
    long_score: float
    short_score: float
    bucket: InterestBucket


class AdminUserInterestState(BaseModel):
    user_id: UUID
    topics: list[AdminInterestTopicView]
    updated_at: datetime


class AdminEventView(BaseModel):
    """`GET /admin/users/{id}/events` — 사용자 행동 로그 row."""

    event_id: UUID
    event_type: EventType
    document_id: UUID | None = None
    cso_topic_id: UUID | None = None
    leaf_topic_id: UUID | None = None
    dwell_ms: int | None = None
    occurred_at: datetime
    server_received_at: datetime


__all__ = [
    "AdminEventView",
    "AdminInterestTopicView",
    "AdminLoginRequest",
    "AdminLogoutRequest",
    "AdminMeResponse",
    "AdminRefreshRequest",
    "AdminTokenPair",
    "AdminUserInterestState",
    "AdminUserListItem",
    "ChangeAdminPasswordRequest",
    "ClickbaitBySource",
    "ClickbaitResultView",
    "ClickbaitStatsResponse",
    "CollectionJobView",
    "CollectionStatsResponse",
    "ReprocessRequestPayload",
    "ReprocessRequestView",
    "SourceTogglePatch",
    "SourceView",
    "TopicLinkageErrorView",
]
