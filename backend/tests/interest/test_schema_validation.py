"""schemas.py Pydantic validation — NotInterestedRequest model_validator + BatchResponse."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.interest.schemas import (
    BatchResponse,
    EventResponse,
    NotInterestedRequest,
)


class TestNotInterestedRequestValidator:
    def test_cso_only_ok(self) -> None:
        req = NotInterestedRequest(
            cso_topic_id=uuid4(), client_request_id="req-1"
        )
        assert req.cso_topic_id is not None

    def test_leaf_only_ok(self) -> None:
        req = NotInterestedRequest(
            leaf_topic_id=uuid4(), client_request_id="req-1"
        )
        assert req.leaf_topic_id is not None

    def test_document_only_ok(self) -> None:
        req = NotInterestedRequest(
            document_id=uuid4(), client_request_id="req-1"
        )
        assert req.document_id is not None

    def test_all_none_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            NotInterestedRequest(client_request_id="req-1")
        assert "필수" in str(exc_info.value) or "minimum" in str(exc_info.value).lower()

    def test_combination_cso_plus_document_ok(self) -> None:
        req = NotInterestedRequest(
            cso_topic_id=uuid4(),
            document_id=uuid4(),
            client_request_id="req-1",
        )
        assert req.cso_topic_id is not None
        assert req.document_id is not None


class TestBatchResponse:
    def test_total_accepted_count(self) -> None:
        now = datetime.now(UTC)
        items = [
            EventResponse(
                event_id=uuid4(),
                accepted=True,
                server_received_at=now,
            ),
            EventResponse(
                event_id=UUID(int=0),
                accepted=False,
                server_received_at=now,
                error_code="event.duplicate",
            ),
        ]
        resp = BatchResponse(items=items, total_accepted=1)
        assert resp.total_accepted == 1
        assert len(resp.items) == 2
        assert resp.items[1].error_code == "event.duplicate"
