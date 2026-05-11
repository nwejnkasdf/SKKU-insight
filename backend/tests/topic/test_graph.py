"""NetworkX 그래프 탐색 6 함수 unit. in-memory DiGraph 직접 구성."""
from __future__ import annotations

from uuid import UUID, uuid4

import networkx as nx
import pytest

from app.topic.graph import (
    find_adjacent,
    find_ancestors,
    find_descendants,
    find_equivalents,
    get_children,
    get_parents,
    graph_distance,
    map_to_clusters,
    verify_cso_import,
)
from app.topic.mapping import EXPECTED_CLUSTERS


@pytest.fixture
def simple_tree() -> tuple[nx.DiGraph, dict[str, UUID]]:
    """4 노드 트리: AI ← ML ← NLP, AI ← CV. 엣지: child → parent."""
    g: nx.DiGraph = nx.DiGraph()
    ids = {
        "ai": uuid4(),
        "ml": uuid4(),
        "nlp": uuid4(),
        "cv": uuid4(),
    }
    for name, node_id in ids.items():
        g.add_node(
            node_id, label=name.upper(), uri=f"uri://{name}", cluster_labels={"AI"}
        )
    # ML 의 부모 = AI, NLP 의 부모 = ML, CV 의 부모 = AI
    g.add_edge(ids["ml"], ids["ai"], type="parent")
    g.add_edge(ids["nlp"], ids["ml"], type="parent")
    g.add_edge(ids["cv"], ids["ai"], type="parent")
    return g, ids


def test_find_adjacent_1hop(simple_tree: tuple[nx.DiGraph, dict[str, UUID]]) -> None:
    """ML 의 1-hop 인접 = AI (부모) + NLP (자식)."""
    g, ids = simple_tree
    adjacent = set(find_adjacent(g, ids["ml"], hops=1))
    assert adjacent == {ids["ai"], ids["nlp"]}


def test_find_adjacent_2hop_expands(
    simple_tree: tuple[nx.DiGraph, dict[str, UUID]],
) -> None:
    """AI 의 2-hop 인접 = ML, CV (직접 자식) + NLP (ML 의 자식)."""
    g, ids = simple_tree
    adjacent = set(find_adjacent(g, ids["ai"], hops=2))
    assert adjacent == {ids["ml"], ids["cv"], ids["nlp"]}


def test_find_adjacent_seed_excluded(
    simple_tree: tuple[nx.DiGraph, dict[str, UUID]],
) -> None:
    """seed 자기 자신은 결과에서 제외."""
    g, ids = simple_tree
    assert ids["ml"] not in find_adjacent(g, ids["ml"], hops=2)


def test_find_adjacent_unknown_id_returns_empty(
    simple_tree: tuple[nx.DiGraph, dict[str, UUID]],
) -> None:
    g, _ = simple_tree
    assert find_adjacent(g, uuid4(), hops=1) == []


def test_find_ancestors(simple_tree: tuple[nx.DiGraph, dict[str, UUID]]) -> None:
    """NLP 의 ancestors = ML + AI. successors 방향 = ancestor."""
    g, ids = simple_tree
    ancestors = set(find_ancestors(g, ids["nlp"]))
    assert ancestors == {ids["ml"], ids["ai"]}


def test_find_descendants(simple_tree: tuple[nx.DiGraph, dict[str, UUID]]) -> None:
    """AI 의 descendants = ML + NLP + CV."""
    g, ids = simple_tree
    descendants = set(find_descendants(g, ids["ai"]))
    assert descendants == {ids["ml"], ids["nlp"], ids["cv"]}


def test_find_equivalents_with_equiv_edge() -> None:
    """type=equiv 엣지만 equivalent 로 반환."""
    g: nx.DiGraph = nx.DiGraph()
    a, b, c = uuid4(), uuid4(), uuid4()
    for n in (a, b, c):
        g.add_node(n, label=str(n), uri="x", cluster_labels=set())
    g.add_edge(a, b, type="equiv")
    g.add_edge(a, c, type="parent")
    equivs = find_equivalents(g, a)
    assert b in equivs
    assert c not in equivs


def test_map_to_clusters(simple_tree: tuple[nx.DiGraph, dict[str, UUID]]) -> None:
    """노드의 cluster_labels set 반환."""
    g, ids = simple_tree
    clusters = map_to_clusters(g, ids["ai"])
    assert clusters == {"AI"}


def test_map_to_clusters_unknown_returns_empty() -> None:
    g: nx.DiGraph = nx.DiGraph()
    assert map_to_clusters(g, uuid4()) == set()


def test_graph_distance(simple_tree: tuple[nx.DiGraph, dict[str, UUID]]) -> None:
    """무방향 거리. ML ↔ CV = 2 (AI 거쳐)."""
    g, ids = simple_tree
    assert graph_distance(g, ids["ml"], ids["cv"]) == 2
    assert graph_distance(g, ids["nlp"], ids["ai"]) == 2


def test_graph_distance_disconnected() -> None:
    g: nx.DiGraph = nx.DiGraph()
    a, b = uuid4(), uuid4()
    g.add_node(a)
    g.add_node(b)
    # 엣지 없음 → 도달 불가
    assert graph_distance(g, a, b) is None


def test_get_parents_children(
    simple_tree: tuple[nx.DiGraph, dict[str, UUID]],
) -> None:
    g, ids = simple_tree
    parents = get_parents(g, ids["ml"])
    children = get_children(g, ids["ai"])
    assert set(parents) == {ids["ai"]}
    assert set(children) == {ids["ml"], ids["cv"]}


def test_verify_cso_import_passes_on_valid_dag() -> None:
    """12 cluster 모두 있는 DAG → 통과."""
    g: nx.DiGraph = nx.DiGraph()
    for cluster in EXPECTED_CLUSTERS:
        node_id = uuid4()
        g.add_node(
            node_id, label=cluster, uri=f"uri://{cluster}", cluster_labels={cluster}
        )
    verify_cso_import(g)  # raise 안 함


def test_verify_cso_import_raises_on_missing_cluster() -> None:
    """1 cluster 누락 → RuntimeError."""
    g: nx.DiGraph = nx.DiGraph()
    for cluster in list(EXPECTED_CLUSTERS)[:11]:  # 11 개만
        node_id = uuid4()
        g.add_node(
            node_id, label=cluster, uri=f"uri://{cluster}", cluster_labels={cluster}
        )
    with pytest.raises(RuntimeError, match="missing clusters"):
        verify_cso_import(g)


def test_verify_cso_import_cycle_warns_but_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """cycle 발견 시 WARN 로그 + RuntimeError 던지지 않음 (§F-5)."""
    g: nx.DiGraph = nx.DiGraph()
    # 12 cluster 모두 + 사이클 추가
    cluster_nodes: list[UUID] = []
    for cluster in EXPECTED_CLUSTERS:
        node_id = uuid4()
        cluster_nodes.append(node_id)
        g.add_node(
            node_id, label=cluster, uri=f"uri://{cluster}", cluster_labels={cluster}
        )
    # 사이클 (A → B → A)
    g.add_edge(cluster_nodes[0], cluster_nodes[1], type="parent")
    g.add_edge(cluster_nodes[1], cluster_nodes[0], type="parent")

    with caplog.at_level("WARNING"):
        verify_cso_import(g)  # cluster 모두 있으므로 cycle 만 WARN
    assert any("cycle" in r.message.lower() for r in caplog.records)
