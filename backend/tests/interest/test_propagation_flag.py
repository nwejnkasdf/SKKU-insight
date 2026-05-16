"""propagation feature flag — false 면 ancestors 미갱신, true 면 trace path 위 조상에 가산."""
from __future__ import annotations

import uuid

import networkx as nx
import pytest

from app.contracts import TraversalStatus
from app.db.models import CSOTopic, User, UserCSOTraversal
from app.interest.config_loader import InterestParams
from app.interest.propagation import compute_ancestor_propagation


class _FakeSettings:
    def __init__(self, propagation_enabled: bool) -> None:
        self.INTEREST_PROPAGATION_ENABLED = propagation_enabled


@pytest.fixture
def params() -> InterestParams:
    return InterestParams(
        alpha_prior=1.0,
        beta_prior=4.0,
        half_life_short_active_days=7,
        half_life_long_active_days=60,
        onboarding_prior_boost=1.0,
        onboarding_boost_active_days=14,
        propagation_hop_decay=0.5,
        propagation_max_hops=4,
        propagation_non_trace_ancestors=False,
        bucket_high_long=0.70,
        bucket_high_short=0.60,
        bucket_medium=0.50,
        bucket_low=0.30,
    )


@pytest.fixture
def graph() -> nx.DiGraph:
    return nx.DiGraph()


@pytest.mark.asyncio
async def test_flag_false_returns_empty(
    db_session,
    seeded_user: User,
    seeded_cso_topics: list[CSOTopic],
    params: InterestParams,
    graph: nx.DiGraph,
) -> None:
    settings = _FakeSettings(propagation_enabled=False)
    result = await compute_ancestor_propagation(
        db_session,
        graph,
        settings,  # type: ignore[arg-type]
        params,
        user_id=seeded_user.user_id,
        leaf_parent_cso_id=seeded_cso_topics[0].cso_topic_id,
    )
    assert result == []


@pytest.mark.asyncio
async def test_flag_true_with_trace_propagates_ancestors(
    db_session,
    seeded_user: User,
    seeded_cso_topics: list[CSOTopic],
    params: InterestParams,
    graph: nx.DiGraph,
) -> None:
    # trace path: root=topic[0] → middle=topic[1] → leaf=topic[2]
    trace = UserCSOTraversal(
        trace_id=uuid.uuid4(),
        user_id=seeded_user.user_id,
        path=[t.cso_topic_id for t in seeded_cso_topics],
        status=TraversalStatus.ACTIVE.value,
        started_active_day=0,
        last_activity_active_day=1,
        score_tail=0.5,
    )
    db_session.add(trace)
    await db_session.flush()

    settings = _FakeSettings(propagation_enabled=True)
    # leaf 토픽 (path[2]) 에서 propagation 호출 → 조상 path[0], path[1] 에 attenuation 가산
    result = await compute_ancestor_propagation(
        db_session,
        graph,
        settings,  # type: ignore[arg-type]
        params,
        user_id=seeded_user.user_id,
        leaf_parent_cso_id=seeded_cso_topics[2].cso_topic_id,
    )
    # path[1] (1-hop), path[0] (2-hop) → 0.5, 0.25
    by_id = {p.cso_topic_id: p.attenuation for p in result}
    assert seeded_cso_topics[1].cso_topic_id in by_id
    assert seeded_cso_topics[0].cso_topic_id in by_id
    assert by_id[seeded_cso_topics[1].cso_topic_id] == 0.5
    assert by_id[seeded_cso_topics[0].cso_topic_id] == 0.25


@pytest.mark.asyncio
async def test_no_active_trace_returns_empty_even_when_flag_true(
    db_session,
    seeded_user: User,
    seeded_cso_topics: list[CSOTopic],
    params: InterestParams,
    graph: nx.DiGraph,
) -> None:
    """trace 없음 → propagation 0."""
    settings = _FakeSettings(propagation_enabled=True)
    result = await compute_ancestor_propagation(
        db_session,
        graph,
        settings,  # type: ignore[arg-type]
        params,
        user_id=seeded_user.user_id,
        leaf_parent_cso_id=seeded_cso_topics[2].cso_topic_id,
    )
    assert result == []
