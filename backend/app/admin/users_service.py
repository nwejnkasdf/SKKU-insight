"""admin users 비즈니스 — GET /admin/users 사용자 목록 (마스킹 포함).

마스킹 규칙 (decision-backlog C-9, api/admin.md 비즈니스 룰):
- super: 전체 email 원문
- operator / read_only: local part 길이별
    - ≥ 2: 첫글자 + `***` + 마지막글자 + `@` + 도메인 (예: g***3@gmail.com)
    - = 1: 전체 fallback ***@gmail.com
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection import service as collection_service
from app.collection.schemas import RunNowResponse
from app.contracts import (
    AdminRole,
    CollectionJobStatus,
    ErrorCode,
    JobType,
    PagedResponse,
    PageMeta,
    RedisKey,
)
from app.db.models import AdminUser, CollectionJob, User, UserConsent

from .schemas import AdminUserListItem


def mask_email(email: str, role: str) -> str:
    """role 별 email 마스킹. super 는 원문, operator/read_only 는 위 규칙."""
    if role == AdminRole.SUPER.value:
        return email
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    if len(local) >= 2:
        return f"{local[0]}***{local[-1]}@{domain}"
    return f"***@{domain}"


def _encode_cursor(created_at: datetime, user_id: UUID) -> str:
    payload = f"{created_at.isoformat()}|{user_id}".encode()
    return base64.urlsafe_b64encode(payload).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID] | None:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_str, uid_str = decoded.split("|", 1)
        return datetime.fromisoformat(ts_str), UUID(uid_str)
    except (ValueError, binascii.Error):
        return None


async def list_users(
    admin: AdminUser,
    *,
    cursor: str | None,
    limit: int,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> PagedResponse[AdminUserListItem]:
    """cursor-based 페이지네이션. consent_active 는 cached lookup (소량이라 OK)."""
    stmt = select(User).where(User.deleted_at.is_(None)).order_by(
        desc(User.created_at), desc(User.user_id)
    )
    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded:
            ts, uid = decoded
            # codex C-6: Python tuple 비교가 먼저 발생해 SQLAlchemy 표현식의 truth value
            # 평가로 이어지면 TypeError. SQL row-comparison 명시 OR/AND 로 표현.
            # 같은 created_at 안에서는 user_id 로 tie-break.
            stmt = stmt.where(
                or_(
                    User.created_at < ts,
                    and_(User.created_at == ts, User.user_id < uid),
                )
            )
    stmt = stmt.limit(limit + 1)  # +1 has_more 검출
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    # consent active 일괄 조회
    if rows:
        consent_stmt = select(UserConsent).where(
            UserConsent.user_id.in_([r.user_id for r in rows]),
            UserConsent.consent_type == "personalization",
            UserConsent.revoked_at.is_(None),
        )
        consent_rows = (await db.execute(consent_stmt)).scalars().all()
        active_user_ids = {c.user_id for c in consent_rows}
    else:
        active_user_ids = set()

    latest_collection_jobs = await _latest_collection_jobs(
        db, [row.user_id for row in rows]
    )

    items = []
    for row in rows:
        deletion_pending = (
            await redis.exists(f"account_deletion:{row.user_id}")
        ) > 0
        latest_job = latest_collection_jobs.get(row.user_id)
        items.append(
            AdminUserListItem(
                user_id=row.user_id,
                email=mask_email(row.email, admin.role),
                created_at=row.created_at,
                consent_active=row.user_id in active_user_ids,
                deletion_pending=deletion_pending,
                latest_collection_status=(
                    CollectionJobStatus(latest_job.status) if latest_job else None
                ),
                latest_collection_created_at=(
                    latest_job.created_at if latest_job else None
                ),
                latest_collection_started_at=(
                    latest_job.started_at if latest_job else None
                ),
                latest_collection_finished_at=(
                    latest_job.finished_at if latest_job else None
                ),
            )
        )

    next_cursor = (
        _encode_cursor(rows[-1].created_at, rows[-1].user_id)
        if has_more and rows
        else None
    )
    _ = RedisKey  # unused import 회피 (lint)
    return PagedResponse[AdminUserListItem](
        items=items,
        meta=PageMeta(
            next_cursor=next_cursor, has_more=has_more, page_size=len(items)
        ),
    )


async def _latest_collection_jobs(
    db: AsyncSession, user_ids: list[UUID]
) -> dict[UUID, CollectionJob]:
    if not user_ids:
        return {}
    stmt = (
        select(CollectionJob)
        .where(
            CollectionJob.user_id.in_(user_ids),
            CollectionJob.job_type == JobType.DAILY_COLLECT.value,
        )
        .order_by(
            CollectionJob.user_id,
            desc(CollectionJob.created_at),
            desc(CollectionJob.job_id),
        )
    )
    rows = list((await db.execute(stmt)).scalars().all())
    latest: dict[UUID, CollectionJob] = {}
    for row in rows:
        if row.user_id is not None and row.user_id not in latest:
            latest[row.user_id] = row
    return latest


async def trigger_user_collection_now(
    admin: AdminUser,
    *,
    user_id: UUID,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> RunNowResponse:
    """관리자가 사용자 1명의 문서 수집을 즉시 큐잉한다.

    사용자용 `/collection/jobs/me/run-now`와 같은 service를 재사용하되,
    관리자 화면에서 오작동하지 않도록 존재/동의 상태만 먼저 확인한다.
    """
    _ = admin
    user = (
        await db.execute(
            select(User).where(User.user_id == user_id, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "사용자를 찾을 수 없습니다.",
            },
        )

    active_consent = (
        await db.execute(
            select(UserConsent.consent_id).where(
                UserConsent.user_id == user_id,
                UserConsent.consent_type == "personalization",
                UserConsent.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if active_consent is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "개인화 동의가 활성화된 사용자만 수집을 실행할 수 있습니다.",
            },
        )

    return await collection_service.trigger_run_now(db, redis, user_id)


__all__ = ["list_users", "mask_email", "trigger_user_collection_now"]
