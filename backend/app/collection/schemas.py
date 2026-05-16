"""collection Pydantic schemas — docs/api/collection.md.

사용자용 endpoint 만 본 모듈이 정의. 관리자 영역 schema 는 본 파일의 공유 모델을
admin/schemas.py 가 import 해 사용한다.

(v13 round 2 Codex S-06, 2026-05-16): `JobType` inline Literal 폐기 → `contracts.JobType`
enum 사용. 헌법 §12 정합 + N-01 type:ignore 자연 해소.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.contracts import CollectionJobStatus, JobType


class CollectionJobView(BaseModel):
    """관리자 응답용 — failure_reason 포함. NFR-08: 사용자에게 노출 X."""

    job_id: UUID
    user_id: UUID
    source_id: UUID | None = None
    target_cso_topic_id: UUID | None = None
    target_leaf_topic_id: UUID | None = None
    job_type: JobType
    status: CollectionJobStatus
    failure_reason: str | None = None
    retry_count: int
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CollectionJobPublicView(BaseModel):
    """사용자 응답용 — failure_reason 마스킹 (NFR-08, collection.md §비즈니스 룰)."""

    job_id: UUID
    user_id: UUID
    source_id: UUID | None = None
    target_cso_topic_id: UUID | None = None
    target_leaf_topic_id: UUID | None = None
    job_type: JobType
    status: CollectionJobStatus
    retry_count: int
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CollectionJobMeResponse(BaseModel):
    """`GET /collection/jobs/me` — 최근 잡 + 7 일 이력. 사용자 응답이라 PublicView 사용.

    (v13 round 2 Codex S-05) cursor pagination 정합 — `next_cursor` / `has_more` 추가.
    클라이언트가 ?cursor 로 다음 페이지 요청 가능.
    """

    latest: CollectionJobPublicView | None = None
    history: list[CollectionJobPublicView]
    next_cursor: str | None = None
    has_more: bool = False


class RunNowResponse(BaseModel):
    """`POST /collection/jobs/me/run-now` — 강제 트리거 즉시 응답."""

    job_id: UUID
    status: Literal["queued"] = "queued"
    eta_seconds: int
