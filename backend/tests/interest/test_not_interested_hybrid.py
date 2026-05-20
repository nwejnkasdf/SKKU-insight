"""not-interested 하이브리드 (정렬 2): Bayesian 분배 + NotInterestedTopic 최고 confidence 1건."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import networkx as nx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import (
    CSOTopic,
    Document,
    HiddenDocument,
    NotInterestedTopic,
    User,
    UserInterestState,
)
from app.interest.config_loader import load_system_config
from app.interest.service import not_interested_feedback


@pytest.fixture
def empty_graph() -> nx.DiGraph:
    return nx.DiGraph()


@pytest.mark.asyncio
async def test_document_id_only_creates_single_not_interested_topic(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_document: Document,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
    empty_graph: nx.DiGraph,
) -> None:
    """document_id 만 호출 → 문서 숨김 + 최고 confidence 토픽 1건 NotInterestedTopic INSERT."""
    settings = get_settings()
    params, weights = await load_system_config(db_session, redis_client)
    await not_interested_feedback(
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
    # NotInterestedTopic 1 row (최고 confidence)
    nit_rows = (
        await db_session.execute(
            select(NotInterestedTopic).where(
                NotInterestedTopic.user_id == seeded_user.user_id
            )
        )
    ).scalars().all()
    assert len(nit_rows) == 1
    # 최고 confidence 는 seeded_cso_topics[0] (0.6)
    assert nit_rows[0].cso_topic_id == seeded_cso_topics[0].cso_topic_id
    hidden_doc = (
        await db_session.execute(
            select(HiddenDocument).where(
                HiddenDocument.user_id == seeded_user.user_id,
                HiddenDocument.document_id == seeded_document.document_id,
            )
        )
    ).scalar_one_or_none()
    assert hidden_doc is not None

    # Bayesian 은 P1-4 분배 → 3 row 모두 beta 가산 (negative weight)
    uis_rows = (
        await db_session.execute(
            select(UserInterestState).where(
                UserInterestState.user_id == seeded_user.user_id
            )
        )
    ).scalars().all()
    assert len(uis_rows) == 3
    # 모든 row 의 beta 가 beta_prior(4.0) 초과
    for row in uis_rows:
        assert row.long_beta > params.beta_prior


@pytest.mark.asyncio
async def test_explicit_topic_creates_single_not_interested(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
    empty_graph: nx.DiGraph,
) -> None:
    """cso_topic_id 명시 → NotInterestedTopic 1 row + Bayesian 100% 단일."""
    settings = get_settings()
    params, weights = await load_system_config(db_session, redis_client)
    target_cso = seeded_cso_topics[1].cso_topic_id
    await not_interested_feedback(
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
    nit = (
        await db_session.execute(
            select(NotInterestedTopic).where(
                NotInterestedTopic.user_id == seeded_user.user_id
            )
        )
    ).scalars().all()
    assert len(nit) == 1
    assert nit[0].cso_topic_id == target_cso
    # Bayesian 은 1 row 만 (단일 토픽 100%)
    uis = (
        await db_session.execute(
            select(UserInterestState).where(
                UserInterestState.user_id == seeded_user.user_id
            )
        )
    ).scalars().all()
    assert len(uis) == 1
    assert uis[0].cso_topic_id == target_cso
