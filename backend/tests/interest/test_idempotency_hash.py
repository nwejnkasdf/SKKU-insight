"""compute_payload_hash 결정성 + payload 차이 감지 (DB X)."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.interest.idempotency import compute_payload_hash


def _ts() -> datetime:
    return datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


class TestPayloadHash:
    def test_deterministic_same_input(self) -> None:
        h1 = compute_payload_hash(
            event_type="click",
            document_id=UUID("11111111-1111-1111-1111-111111111111"),
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            occurred_at=_ts(),
        )
        h2 = compute_payload_hash(
            event_type="click",
            document_id=UUID("11111111-1111-1111-1111-111111111111"),
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            occurred_at=_ts(),
        )
        assert h1 == h2
        assert len(h1) == 64

    def test_document_id_change(self) -> None:
        h1 = compute_payload_hash(
            event_type="click",
            document_id=UUID("11111111-1111-1111-1111-111111111111"),
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            occurred_at=_ts(),
        )
        h2 = compute_payload_hash(
            event_type="click",
            document_id=UUID("22222222-2222-2222-2222-222222222222"),
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            occurred_at=_ts(),
        )
        assert h1 != h2

    def test_event_type_change(self) -> None:
        h1 = compute_payload_hash(
            event_type="click",
            document_id=None,
            cso_topic_id=UUID("11111111-1111-1111-1111-111111111111"),
            leaf_topic_id=None,
            dwell_ms=None,
            occurred_at=_ts(),
        )
        h2 = compute_payload_hash(
            event_type="save",
            document_id=None,
            cso_topic_id=UUID("11111111-1111-1111-1111-111111111111"),
            leaf_topic_id=None,
            dwell_ms=None,
            occurred_at=_ts(),
        )
        assert h1 != h2

    def test_dwell_ms_change(self) -> None:
        h1 = compute_payload_hash(
            event_type="dwell_tick",
            document_id=UUID("11111111-1111-1111-1111-111111111111"),
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=30000,
            occurred_at=_ts(),
        )
        h2 = compute_payload_hash(
            event_type="dwell_tick",
            document_id=UUID("11111111-1111-1111-1111-111111111111"),
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=60000,
            occurred_at=_ts(),
        )
        assert h1 != h2

    def test_occurred_at_change(self) -> None:
        h1 = compute_payload_hash(
            event_type="click",
            document_id=UUID("11111111-1111-1111-1111-111111111111"),
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            occurred_at=_ts(),
        )
        h2 = compute_payload_hash(
            event_type="click",
            document_id=UUID("11111111-1111-1111-1111-111111111111"),
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            occurred_at=datetime(2026, 5, 17, 12, 0, 1, tzinfo=UTC),
        )
        assert h1 != h2
