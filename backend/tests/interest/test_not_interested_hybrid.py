"""not_interested feedback separation.

Document-only feedback hides that document without changing topic posterior.
Explicit topic feedback keeps the original topic-level penalty path.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import networkx as nx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.contracts import EventType
from app.db.models import (
    CSOTopic,
    Document,
    HiddenDocument,
    NotInterestedTopic,
    User,
    UserEvent,
    UserInterestState,
)
from app.interest.config_loader import load_system_config
from app.interest.service import not_interested_feedback


@pytest.fixture
def empty_graph() -> nx.DiGraph:
    return nx.DiGraph()


@pytest.mark.asyncio
async def test_document_id_only_hides_document_without_topic_penalty(
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

    result = await not_interested_feedback(
        db_session,
        redis_client,
        empty_graph,
        settings,
        params,
        weights,
        user=seeded_user,
        document_id=seeded_document.document_id,
        cso_topic_id=None,
        leaf_topic_id=None,
        client_request_id=f"req-{uuid.uuid4().hex[:8]}",
        occurred_at=datetime.now(UTC),
        active_day=seeded_user.active_day_counter,
    )

    assert result.posterior_applied is False
    hidden_doc = (
        await db_session.execute(
            select(HiddenDocument).where(
                HiddenDocument.user_id == seeded_user.user_id,
                HiddenDocument.document_id == seeded_document.document_id,
            )
        )
    ).scalar_one_or_none()
    assert hidden_doc is not None

    nit_count = (
        await db_session.execute(
            select(func.count()).select_from(NotInterestedTopic).where(
                NotInterestedTopic.user_id == seeded_user.user_id
            )
        )
    ).scalar_one()
    assert nit_count == 0

    state_count = (
        await db_session.execute(
            select(func.count()).select_from(UserInterestState).where(
                UserInterestState.user_id == seeded_user.user_id
            )
        )
    ).scalar_one()
    assert state_count == 0

    event = (
        await db_session.execute(
            select(UserEvent).where(
                UserEvent.user_id == seeded_user.user_id,
                UserEvent.document_id == seeded_document.document_id,
                UserEvent.event_type == EventType.NOT_INTERESTED.value,
            )
        )
    ).scalar_one()
    assert event.active_day_at_event == seeded_user.active_day_counter


@pytest.mark.asyncio
async def test_explicit_topic_creates_single_not_interested(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
    empty_graph: nx.DiGraph,
) -> None:
    settings = get_settings()
    params, weights = await load_system_config(db_session, redis_client)
    target_cso = seeded_cso_topics[1].cso_topic_id

    result = await not_interested_feedback(
        db_session,
        redis_client,
        empty_graph,
        settings,
        params,
        weights,
        user=seeded_user,
        document_id=None,
        cso_topic_id=target_cso,
        leaf_topic_id=None,
        client_request_id=f"req-{uuid.uuid4().hex[:8]}",
        occurred_at=datetime.now(UTC),
        active_day=seeded_user.active_day_counter,
    )

    assert result.posterior_applied is True
    nit = (
        await db_session.execute(
            select(NotInterestedTopic).where(
                NotInterestedTopic.user_id == seeded_user.user_id
            )
        )
    ).scalars().all()
    assert len(nit) == 1
    assert nit[0].cso_topic_id == target_cso

    uis = (
        await db_session.execute(
            select(UserInterestState).where(
                UserInterestState.user_id == seeded_user.user_id
            )
        )
    ).scalars().all()
    assert len(uis) == 1
    assert uis[0].cso_topic_id == target_cso
