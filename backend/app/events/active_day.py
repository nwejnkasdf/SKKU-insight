"""active_day_counter atomic UPDATE — concurrency.md §4.2.

사용자 그날 첫 인터랙션이면 User.active_day_counter += 1, 동시 호출 idempotent.
`WHERE last_active_calendar_date < :today` 가 atomic 가드 — 두 번째 호출은 0건 갱신.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def maybe_increment_active_day(
    db: AsyncSession, user_id: UUID, today: date
) -> int:
    """오늘 첫 인터랙션이면 active_day_counter +1. 이미 카운트됐으면 현재 값 반환.

    동시 두 이벤트가 들어와도 첫 번째 호출만 UPDATE 성공 (WHERE 가드).
    """
    result = await db.execute(
        text(
            """
            UPDATE "user"
            SET active_day_counter = active_day_counter + 1,
                last_active_calendar_date = :today
            WHERE user_id = :user_id
              AND (last_active_calendar_date IS NULL OR last_active_calendar_date < :today)
            RETURNING active_day_counter
            """
        ),
        {"user_id": user_id, "today": today},
    )
    row = result.first()
    if row is not None:
        await db.flush()
        return int(row.active_day_counter)
    # 이미 오늘 카운트됨 — 현재 값 SELECT
    result2 = await db.execute(
        text('SELECT active_day_counter FROM "user" WHERE user_id = :user_id'),
        {"user_id": user_id},
    )
    value = result2.scalar_one()
    return int(value)


__all__ = ["maybe_increment_active_day"]
