"""EventBuffer — 5초 batch flush + size cap + stop final flush. DB X."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.events.buffer import BufferedEvent, EventBuffer
from app.interest.schemas import EventRequest


def _entry(user_id: UUID, active_day: int = 1) -> BufferedEvent:
    return BufferedEvent(
        user_id=user_id,
        request=EventRequest(
            event_type="click",
            document_id=None,
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            occurred_at=datetime.now(UTC),
            client_request_id=f"req-{uuid4().hex[:8]}",
        ),
        payload_hash="x" * 64,
        server_received_at=datetime.now(UTC),
        active_day_counter=active_day,
    )


@pytest.mark.asyncio
async def test_cap_triggers_flush() -> None:
    flushed: list[tuple[UUID, list[BufferedEvent]]] = []

    async def _cb(uid: UUID, entries: Iterable[BufferedEvent]) -> None:
        flushed.append((uid, list(entries)))

    buf = EventBuffer(flush_callback=_cb, batch_size=3, flush_seconds=999.0)
    uid = uuid4()
    for _ in range(3):
        await buf.add(_entry(uid))
    assert len(flushed) == 1
    assert flushed[0][0] == uid
    assert len(flushed[0][1]) == 3
    assert buf.pending_count() == 0


@pytest.mark.asyncio
async def test_flush_now_returns_count() -> None:
    flushed: list[BufferedEvent] = []

    async def _cb(uid: UUID, entries: Iterable[BufferedEvent]) -> None:
        flushed.extend(entries)

    buf = EventBuffer(flush_callback=_cb, batch_size=999, flush_seconds=999.0)
    uid = uuid4()
    await buf.add(_entry(uid))
    await buf.add(_entry(uid))
    count = await buf.flush_now()
    assert count == 2
    assert len(flushed) == 2


@pytest.mark.asyncio
async def test_per_user_grouping() -> None:
    flushed_by_user: dict[UUID, int] = {}

    async def _cb(uid: UUID, entries: Iterable[BufferedEvent]) -> None:
        flushed_by_user[uid] = flushed_by_user.get(uid, 0) + len(list(entries))

    buf = EventBuffer(flush_callback=_cb, batch_size=10, flush_seconds=999.0)
    u1 = uuid4()
    u2 = uuid4()
    await buf.add(_entry(u1))
    await buf.add(_entry(u2))
    await buf.add(_entry(u1))
    await buf.flush_now()
    assert flushed_by_user == {u1: 2, u2: 1}


@pytest.mark.asyncio
async def test_stop_final_flush() -> None:
    flushed: list[BufferedEvent] = []

    async def _cb(uid: UUID, entries: Iterable[BufferedEvent]) -> None:
        flushed.extend(entries)

    buf = EventBuffer(flush_callback=_cb, batch_size=999, flush_seconds=999.0)
    uid = uuid4()
    await buf.add(_entry(uid))
    await buf.stop()
    assert len(flushed) == 1


@pytest.mark.asyncio
async def test_flush_callback_exception_does_not_break_buffer() -> None:
    """callback 예외 시 entry drop, buffer 자체는 정상."""
    call_count = 0

    async def _cb(uid: UUID, entries: Iterable[BufferedEvent]) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated")

    buf = EventBuffer(flush_callback=_cb, batch_size=999, flush_seconds=999.0)
    uid = uuid4()
    await buf.add(_entry(uid))
    await buf.flush_now()
    # 두 번째 add + flush 는 정상 동작 (call_count == 2)
    await buf.add(_entry(uid))
    await buf.flush_now()
    assert call_count == 2


@pytest.mark.asyncio
async def test_flush_periodic_cancels_cleanly() -> None:
    """flush_periodic task cancellation 가 예외 없이 종료."""

    async def _cb(uid: UUID, entries: Iterable[BufferedEvent]) -> None:
        pass

    buf = EventBuffer(flush_callback=_cb, batch_size=10, flush_seconds=0.01)
    task = asyncio.create_task(buf.flush_periodic())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert buf.pending_count() == 0
