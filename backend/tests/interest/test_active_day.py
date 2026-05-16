"""maybe_increment_active_day atomic counter — concurrency.md §4.2.

사용자 그날 첫 인터랙션 +1, 동시 호출 idempotent.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.db.models import User
from app.events.active_day import maybe_increment_active_day


@pytest.mark.asyncio
async def test_first_interaction_today_increments(
    db_session, seeded_user: User
) -> None:
    today = date.today()
    # seeded_user 는 last_active_calendar_date=today, counter=1 으로 시작
    # → 오늘 첫 호출이지만 이미 카운트됨 → 변동 없음
    initial = seeded_user.active_day_counter
    value = await maybe_increment_active_day(
        db_session, seeded_user.user_id, today
    )
    assert value == initial


@pytest.mark.asyncio
async def test_new_day_increments(db_session, seeded_user: User) -> None:
    # 어제 날짜로 호출 시뮬레이션 (last_active_calendar_date 가 어제이면 오늘 새 날)
    tomorrow = date.today() + timedelta(days=1)
    initial = seeded_user.active_day_counter
    value = await maybe_increment_active_day(
        db_session, seeded_user.user_id, tomorrow
    )
    assert value == initial + 1


@pytest.mark.asyncio
async def test_concurrent_calls_idempotent(
    db_session, seeded_user: User
) -> None:
    """동일 날짜로 동시 5 호출 → 첫 호출만 +1, 나머지는 같은 값."""
    tomorrow = date.today() + timedelta(days=1)
    initial = seeded_user.active_day_counter
    # NOTE: db_session 은 단일 connection 이라 동시 호출이 sequential 됨 — 본 테스트는
    # WHERE 가드 정합성 검증 (5번 호출해도 counter +1 만)
    values = []
    for _ in range(5):
        v = await maybe_increment_active_day(
            db_session, seeded_user.user_id, tomorrow
        )
        values.append(v)
    assert all(v == initial + 1 for v in values)
