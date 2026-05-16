"""A6 event 패키지 — EventBuffer (5초 batch flush) + active_day atomic counter.

interest/ 와 분리: events/ 는 transport-level concern (batch · idempotency · active_day),
interest/ 는 베이지안 도메인 로직 (사후 갱신 · decay · propagation).
"""
from __future__ import annotations

from app.events.active_day import maybe_increment_active_day
from app.events.buffer import BufferedEvent, EventBuffer

__all__ = [
    "BufferedEvent",
    "EventBuffer",
    "maybe_increment_active_day",
]
