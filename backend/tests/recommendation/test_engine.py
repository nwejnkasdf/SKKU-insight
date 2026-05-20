from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.recommendation import engine


@pytest.mark.asyncio
async def test_active_trace_leaves_cold_start_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Active trace가 있으면 pseudo cold-start row 여부와 무관하게 정상 랭킹 경로로 간다."""

    async def fake_count_active_traces(*_args: object, **_kwargs: object) -> int:
        return 1

    async def fail_if_called(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("pseudo cold-start rows must not keep active traces cold")

    monkeypatch.setattr(
        engine.trav_queries, "count_active_traces", fake_count_active_traces
    )
    monkeypatch.setattr(engine, "_has_only_cold_start_recommendations", fail_if_called)

    user = SimpleNamespace(user_id=uuid4())

    assert await engine._is_cold_start(SimpleNamespace(), user) is False
