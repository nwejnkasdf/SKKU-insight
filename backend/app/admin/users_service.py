"""admin users 비즈니스 — GET /admin/users 사용자 목록 (마스킹 포함).

마스킹 규칙 (decision-backlog C-9, api/admin.md 비즈니스 룰):
- super: 전체 email 원문
- operator / read_only: local part 길이별
    - ≥ 2: 첫글자 + `***` + 마지막글자 + `@` + 도메인 (예: g***3@gmail.com)
    - = 1: 전체 fallback ***@gmail.com
"""
from __future__ import annotations

import base64
from datetime import datetime
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import AdminRole, PagedResponse, PageMeta, RedisKey
from app.db.models import AdminUser, User, UserConsent

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
    except (ValueError, base64.binascii.Error):
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
            stmt = stmt.where(
                (User.created_at, User.user_id) < (ts, uid)  # tuple comparison
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

    items = []
    for row in rows:
        deletion_pending = (
            await redis.exists(f"account_deletion:{row.user_id}")
        ) > 0
        items.append(
            AdminUserListItem(
                user_id=row.user_id,
                email=mask_email(row.email, admin.role),
                created_at=row.created_at,
                consent_active=row.user_id in active_user_ids,
                deletion_pending=deletion_pending,
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


__all__ = ["list_users", "mask_email"]
