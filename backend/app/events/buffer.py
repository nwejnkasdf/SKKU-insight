"""EventBuffer — 5초 batch flush (concurrency.md §6).

view/click/dwell_tick/open_external 이벤트를 in-memory 로 모았다가 5초마다 또는 cap
(`EVENT_BATCH_SIZE`, default 20) 도달 시 flush. flush 시 per-user grouping → 사용자별
Redis mutex (`RedisKey.interest_decay_lock` 와 분리된 별도 lock) 안에서
`ingest_event_atomic` 일괄 호출.

save/hide/not_interested 는 buffer 미사용 — 즉시 응답 (api/interest.md).

lifespan startup 가 `flush_periodic` task 등록, shutdown 가 cancel + final flush.

실패 entry 는 1차 시연에선 drop + WARN (Codex round-2 fix 후 retry 고려).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

if TYPE_CHECKING:

    from app.interest.schemas import EventRequest

logger = structlog.get_logger("events.buffer")
_std_logger = logging.getLogger("events.buffer")


@dataclass
class BufferedEvent:
    """버퍼에 적재되는 event entry. service 가 INSERT 시점에 모두 필요한 필드 포함."""

    user_id: UUID
    request: EventRequest
    payload_hash: str
    server_received_at: datetime
    active_day_counter: int


@dataclass
class EventBuffer:
    """5초 batch flush + size cap. asyncio.Lock 으로 buffer 보호.

    `flush_callback` 은 flush 시 (user_id, list[BufferedEvent]) 호출. service.flush_buffered_events
    가 default. 테스트는 callback 을 override 해 단순 sink 로 사용 가능.
    """

    flush_callback: FlushCallback
    batch_size: int = 20
    flush_seconds: float = 5.0
    _buffer: list[BufferedEvent] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _stopped: bool = False

    async def add(self, item: BufferedEvent) -> None:
        """buffer 에 추가. cap 도달 시 즉시 flush_inner 호출.

        Codex S-04 fix: stop() 가 _stopped=True 셋팅 후 final flush 진행 중에 add()
        호출 시 lock 밖에서 _stopped 검사하면 item 유실 가능. lock 안에서 검사 후
        rejected 시 directly fallback flush (즉시 callback 호출) — 데이터 손실 방지.
        """
        items_to_flush: list[BufferedEvent] | None = None
        async with self._lock:
            if self._stopped:
                # shutdown 중 — buffer 에 추가하지 않고 즉시 callback 호출 (fallback).
                items_to_flush = [item]
            else:
                self._buffer.append(item)
                if len(self._buffer) >= self.batch_size:
                    items_to_flush = self._buffer[:]
                    self._buffer.clear()
        if items_to_flush is not None:
            await self._safe_flush(items_to_flush)

    async def flush_now(self) -> int:
        """수동 flush — buffer 의 모든 entry 처리. 반환: flush 된 entry 수."""
        async with self._lock:
            if not self._buffer:
                return 0
            items_to_flush = self._buffer[:]
            self._buffer.clear()
        await self._safe_flush(items_to_flush)
        return len(items_to_flush)

    async def flush_periodic(self) -> None:
        """5초 주기 background task. shutdown 시 cancel 됨 — final flush 는 stop() 가 보장."""
        while not self._stopped:
            try:
                await asyncio.sleep(self.flush_seconds)
            except asyncio.CancelledError:
                break
            try:
                await self.flush_now()
            except Exception as exc:
                logger.warning("event_buffer.flush_periodic_error", error=str(exc))

    async def stop(self) -> None:
        """final flush + 추가 add 차단."""
        self._stopped = True
        try:
            count = await self.flush_now()
            if count:
                logger.info("event_buffer.final_flush", entries=count)
        except Exception as exc:
            logger.warning("event_buffer.final_flush_error", error=str(exc))

    async def _safe_flush(self, items: list[BufferedEvent]) -> None:
        """flush_callback 호출. 사용자별 group 후 callback 1회씩."""
        if not items:
            return
        by_user: dict[UUID, list[BufferedEvent]] = {}
        for entry in items:
            by_user.setdefault(entry.user_id, []).append(entry)
        for user_id, user_entries in by_user.items():
            try:
                await self.flush_callback(user_id, user_entries)
            except Exception as exc:
                _std_logger.warning(
                    "event_buffer flush failed user_id=%s entries=%d error=%s",
                    user_id,
                    len(user_entries),
                    exc,
                )

    def pending_count(self) -> int:
        """모니터링 용 — lock 없는 snapshot. race 가능하지만 metric 목적이면 OK."""
        return len(self._buffer)


# typing helper — flush_callback signature.
from collections.abc import Awaitable, Callable  # noqa: E402

FlushCallback = Callable[[UUID, Iterable[BufferedEvent]], Awaitable[None]]


__all__ = ["BufferedEvent", "EventBuffer", "FlushCallback"]
