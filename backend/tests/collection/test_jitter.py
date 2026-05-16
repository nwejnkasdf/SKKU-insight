"""collection.orchestrator.deterministic_jitter_seconds unit tests."""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from app.collection.orchestrator import deterministic_jitter_seconds


def test_jitter_deterministic_same_user_day() -> None:
    uid = UUID("11111111-1111-1111-1111-111111111111")
    day = date(2026, 5, 16)
    assert deterministic_jitter_seconds(uid, day) == deterministic_jitter_seconds(uid, day)


def test_jitter_within_cap() -> None:
    uid = UUID("22222222-2222-2222-2222-222222222222")
    day = date(2026, 5, 16)
    assert 0 <= deterministic_jitter_seconds(uid, day, cap=300) < 300


def test_jitter_different_users_distinct() -> None:
    day = date(2026, 5, 16)
    seen = {deterministic_jitter_seconds(uuid4(), day) for _ in range(50)}
    # 50회 중 최소 30 이상 distinct (uniform 분포 가정 — false positive 가드)
    assert len(seen) >= 30


def test_jitter_changes_with_day() -> None:
    uid = UUID("33333333-3333-3333-3333-333333333333")
    distinct = {
        deterministic_jitter_seconds(uid, date(2026, 5, d)) for d in range(1, 21)
    }
    assert len(distinct) >= 10  # 20일 중 10일 이상 다른 값
