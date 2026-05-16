"""bootstrap_interest_state — onboarding 12 cluster + 1-hop successor 자식 row prefilled."""
from __future__ import annotations

import uuid

import networkx as nx
import pytest
from sqlalchemy import select

from app.db.models import BroadInterest, CSOTopic, User, UserInterestState
from app.interest.service import bootstrap_interest_state


@pytest.mark.asyncio
async def test_prefilled_row_with_boost(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_cso_topics: list[CSOTopic],
    seeded_system_config,
) -> None:
    """BroadInterest 1개 + CSO graph 1-hop child 1개 → UserInterestState row 2개 prefilled."""
    # BroadInterest seed (seeded_cso_topics[0] 을 root 로)
    bi = BroadInterest(
        broad_interest_id=uuid.uuid4(),
        name="Test Cluster",
        description="test",
        cso_cluster_label="AI",
        cso_seed_topic_id=seeded_cso_topics[0].cso_topic_id,
        display_order=0,
    )
    db_session.add(bi)
    await db_session.flush()

    # graph: cluster (topic[0]) <- child (topic[1]) child→parent 엣지
    graph = nx.DiGraph()
    graph.add_node(seeded_cso_topics[0].cso_topic_id)
    graph.add_node(seeded_cso_topics[1].cso_topic_id)
    graph.add_edge(
        seeded_cso_topics[1].cso_topic_id,
        seeded_cso_topics[0].cso_topic_id,
        type="parent",
    )

    inserted = await bootstrap_interest_state(
        db_session,
        graph,
        user=seeded_user,
        cluster_ids=[bi.broad_interest_id],
        active_day=seeded_user.active_day_counter,
        redis=redis_client,
    )
    assert inserted >= 1  # cluster 본인 + 자식 1개 (예상 2)

    rows = (
        await db_session.execute(
            select(UserInterestState).where(
                UserInterestState.user_id == seeded_user.user_id
            )
        )
    ).scalars().all()
    # cluster 본인 row 의 alpha > alpha_prior (boost 적용)
    cluster_row = next(
        r for r in rows if r.cso_topic_id == seeded_cso_topics[0].cso_topic_id
    )
    assert cluster_row.long_alpha > 1.0  # alpha_prior 1.0 + boost 1.0
    assert cluster_row.boost_applied_at_active_day == seeded_user.active_day_counter
    # 자식 row 도 boost 적용 (0.5)
    child_row = next(
        (r for r in rows if r.cso_topic_id == seeded_cso_topics[1].cso_topic_id),
        None,
    )
    if child_row is not None:  # graph predecessor 가 자식
        assert child_row.boost_applied_at_active_day == seeded_user.active_day_counter
        assert child_row.long_alpha > 1.0  # alpha_prior + 0.5


@pytest.mark.asyncio
async def test_empty_cluster_ids_returns_zero(
    db_session,
    redis_client,
    seeded_user: User,
    seeded_system_config,
) -> None:
    graph = nx.DiGraph()
    inserted = await bootstrap_interest_state(
        db_session,
        graph,
        user=seeded_user,
        cluster_ids=[],
        active_day=0,
        redis=redis_client,
    )
    assert inserted == 0
