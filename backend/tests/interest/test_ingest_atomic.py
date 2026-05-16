"""ingest_event_atomic 단일 event end-to-end (DB + Redis).

idempotency / dwell cap / Bayesian UPSERT 핵심 흐름 검증.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import networkx as nx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.contracts import EventType
from app.db.models import (
    CSOTopic,
    Document,
    User,
    UserEvent,
    UserInterestState,
)
from app.interest.config_loader import (
    load_system_config,
)
from app.interest.service import ingest_event_atomic


@pytest.fixture
def empty_graph() -> nx.DiGraph:
    return nx.DiGraph()


@pytest.mark.asyncio
async def test_click_event_creates_user_event_and_interest_state(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_document: Document,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
    empty_graph: nx.DiGraph,
) -> None:
    settings = get_settings()
    params, weights = await load_system_config(db_session, redis_client)
    result = await ingest_event_atomic(
        db_session,
        redis_client,
        empty_graph,
        settings,
        params,
        weights,
        user=seeded_user,
        event_type=EventType.CLICK,
        document_id=seeded_document.document_id,
        cso_topic_id=None,
        leaf_topic_id=None,
        dwell_ms=None,
        client_request_id=f"req-{uuid.uuid4().hex[:8]}",
        occurred_at=datetime.now(UTC),
        active_day=seeded_user.active_day_counter,
    )
    assert result.accepted is True
    assert result.posterior_applied is True
    assert result.duplicate is False

    # UserEvent 가 INSERT 됨
    ue_count = (
        await db_session.execute(
            select(UserEvent).where(UserEvent.user_id == seeded_user.user_id)
        )
    ).scalars().all()
    assert len(ue_count) == 1

    # UserInterestState 3 row INSERT (DocumentTopic 3 토픽 분배)
    uis_rows = (
        await db_session.execute(
            select(UserInterestState).where(
                UserInterestState.user_id == seeded_user.user_id
            )
        )
    ).scalars().all()
    assert len(uis_rows) == 3
    # alpha_prior(1.0) + click(1.0) * confidence_normalized > 1.0
    for row in uis_rows:
        assert row.long_alpha > params.alpha_prior


@pytest.mark.asyncio
async def test_idempotent_duplicate_returns_existing(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_document: Document,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
    empty_graph: nx.DiGraph,
) -> None:
    """같은 (user_id, client_request_id) + 같은 payload → 두 번째 호출 200 + duplicate=True."""
    settings = get_settings()
    params, weights = await load_system_config(db_session, redis_client)
    req_id = f"req-{uuid.uuid4().hex[:8]}"
    occurred = datetime.now(UTC)
    args = {
        "db": db_session,
        "redis": redis_client,
        "cso_graph": empty_graph,
        "settings": settings,
        "params": params,
        "weights": weights,
        "user": seeded_user,
        "event_type": EventType.CLICK,
        "document_id": seeded_document.document_id,
        "cso_topic_id": None,
        "leaf_topic_id": None,
        "dwell_ms": None,
        "client_request_id": req_id,
        "occurred_at": occurred,
        "active_day": seeded_user.active_day_counter,
    }
    first = await ingest_event_atomic(**args)
    second = await ingest_event_atomic(**args)
    assert second.duplicate is True
    assert second.event_id == first.event_id


@pytest.mark.asyncio
async def test_view_event_skips_posterior(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_document: Document,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
    empty_graph: nx.DiGraph,
) -> None:
    """view weight=0 → posterior_applied=False, UserEvent 는 INSERT."""
    settings = get_settings()
    params, weights = await load_system_config(db_session, redis_client)
    result = await ingest_event_atomic(
        db_session,
        redis_client,
        empty_graph,
        settings,
        params,
        weights,
        user=seeded_user,
        event_type=EventType.VIEW,
        document_id=seeded_document.document_id,
        cso_topic_id=None,
        leaf_topic_id=None,
        dwell_ms=None,
        client_request_id=f"req-{uuid.uuid4().hex[:8]}",
        occurred_at=datetime.now(UTC),
        active_day=seeded_user.active_day_counter,
    )
    assert result.posterior_applied is False
    # UserInterestState row 없음
    uis_count = (
        await db_session.execute(
            select(UserInterestState).where(
                UserInterestState.user_id == seeded_user.user_id
            )
        )
    ).scalars().all()
    assert len(uis_count) == 0


@pytest.mark.asyncio
async def test_dwell_tick_cap_skips_posterior_after_4(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_document: Document,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
    empty_graph: nx.DiGraph,
) -> None:
    """dwell_tick 6회 → 4회만 posterior, 5/6회 skip (UserEvent 는 6 row 모두)."""
    settings = get_settings()
    params, weights = await load_system_config(db_session, redis_client)
    posterior_applied_flags: list[bool] = []
    for i in range(6):
        result = await ingest_event_atomic(
            db_session,
            redis_client,
            empty_graph,
            settings,
            params,
            weights,
            user=seeded_user,
            event_type=EventType.DWELL_TICK,
            document_id=seeded_document.document_id,
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=30000,
            client_request_id=f"req-dwell-{i}",
            occurred_at=datetime(2026, 5, 17, 12, i, 0, tzinfo=UTC),
            active_day=seeded_user.active_day_counter,
        )
        posterior_applied_flags.append(result.posterior_applied)
    # 첫 4회 True, 마지막 2회 False
    assert posterior_applied_flags[:4] == [True, True, True, True]
    assert posterior_applied_flags[4:] == [False, False]
    # UserEvent 6 row 모두 INSERT
    ue_rows = (
        await db_session.execute(
            select(UserEvent).where(
                UserEvent.user_id == seeded_user.user_id,
                UserEvent.event_type == "dwell_tick",
            )
        )
    ).scalars().all()
    assert len(ue_rows) == 6
