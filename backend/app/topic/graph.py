"""CSO NetworkX 그래프 빌드 + 탐색 6 함수.

cso-mapping.md §그래프 탐색 알고리즘 의사 코드 그대로 구현.

엣지 방향 규약: `child --parent_of--> parent` (자식 → 부모). 따라서:
- `g.successors(n)` = 부모 방향
- `g.predecessors(n)` = 자식 방향
- `nx.descendants(g, n)` = 모든 조상 (find_ancestors)
- `nx.ancestors(g, n)` = 모든 후손 (find_descendants)

A3 결정 18: `build_cso_graph` 는 cso_topic_parent M:N 테이블만 사용. CSOTopic.parent_topic_id
(deprecate 예정) 는 무시. 다중 부모 자연 보존.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import CSOTopic, CSOTopicParent
from app.topic.mapping import EXPECTED_CLUSTERS

logger = logging.getLogger(__name__)


async def build_cso_graph(engine: AsyncEngine) -> nx.DiGraph:
    """DB → NetworkX DiGraph. startup hook 에서 호출.

    노드 attr: label, uri, cluster_labels (set).
    엣지 type: "parent" (child → parent).

    cso_topic_parent SOR. parent_topic_id 무시 (A3 결정 18).
    """
    g: nx.DiGraph = nx.DiGraph()
    async with engine.connect() as conn:
        # 1. 노드 — 모든 CSO 토픽
        result = await conn.execute(
            select(
                CSOTopic.cso_topic_id,
                CSOTopic.label,
                CSOTopic.uri,
                CSOTopic.cluster_labels,
            )
        )
        for row in result:
            g.add_node(
                row.cso_topic_id,
                label=row.label,
                uri=row.uri,
                cluster_labels=set(row.cluster_labels or []),
            )

        # 2. 엣지 — cso_topic_parent SOR (다중 부모 보존, parent_topic_id 무시)
        result2 = await conn.execute(
            select(
                CSOTopicParent.cso_topic_id,
                CSOTopicParent.parent_cso_topic_id,
            )
        )
        for row2 in result2:
            # child --parent--> parent
            g.add_edge(row2.cso_topic_id, row2.parent_cso_topic_id, type="parent")

    logger.info(
        "CSO graph nodes=%d edges=%d", g.number_of_nodes(), g.number_of_edges()
    )
    return g


def verify_cso_import(g: nx.DiGraph) -> None:
    """그래프 무결성 검증 — DAG + 12 cluster 매핑 확인.

    cycle 발견 시 WARN 로그 + 그래프는 build 유지 (startup 막지 않음, §F-5).
    cluster 누락 시 RuntimeError (startup 차단 — 12 seed 매칭 누락은 치명적).
    """
    if not nx.is_directed_acyclic_graph(g):
        cycle: list[UUID] = next(iter(nx.simple_cycles(g)), [])
        logger.warning(
            "CSO graph contains cycle (length=%d): %s — graph build kept",
            len(cycle),
            cycle[:5],
        )
    cluster_labels_seen: set[str] = set()
    for _, data in g.nodes(data=True):
        cluster_labels_seen.update(data.get("cluster_labels", set()))
    missing = set(EXPECTED_CLUSTERS) - cluster_labels_seen
    if missing:
        raise RuntimeError(
            f"CSO graph missing clusters: {sorted(missing)} (seed label 매칭 실패)"
        )
    logger.info(
        "CSO graph verified: nodes=%d edges=%d clusters=%d",
        g.number_of_nodes(),
        g.number_of_edges(),
        len(cluster_labels_seen),
    )


def find_adjacent(g: nx.DiGraph, seed_id: UUID, hops: int = 1) -> list[UUID]:
    """씨드에서 hops 거리 인접 토픽 (부모·자식·equiv). seed_id 자기 자신 제외.

    cso-mapping.md §1 인접 토픽 의사 코드 그대로.
    """
    if seed_id not in g:
        return []
    visited: set[UUID] = {seed_id}
    frontier: set[UUID] = {seed_id}
    for _ in range(hops):
        next_frontier: set[UUID] = set()
        for n in frontier:
            # 부모 (successors) + 자식 (predecessors) + equiv 양방향
            next_frontier.update(g.predecessors(n))
            next_frontier.update(g.successors(n))
            for nb, edge_data in g[n].items():
                if edge_data.get("type") == "equiv":
                    next_frontier.add(nb)
        next_frontier -= visited
        if not next_frontier:
            break
        visited.update(next_frontier)
        frontier = next_frontier
    return list(visited - {seed_id})


def find_ancestors(g: nx.DiGraph, seed_id: UUID) -> list[UUID]:
    """상위 토픽 (부모 방향) 모두. successors=부모 → nx.descendants 가 ancestors."""
    if seed_id not in g:
        return []
    return list(nx.descendants(g, seed_id))


def find_descendants(g: nx.DiGraph, seed_id: UUID) -> list[UUID]:
    """후손 토픽 (자식 방향) 모두. predecessors=자식 → nx.ancestors 가 descendants."""
    if seed_id not in g:
        return []
    return list(nx.ancestors(g, seed_id))


def find_equivalents(g: nx.DiGraph, seed_id: UUID) -> list[UUID]:
    """동등 토픽 (relatedEquivalent 엣지). 1차는 미사용 (cso-import.md §2)."""
    if seed_id not in g:
        return []
    return [nb for nb, data in g[seed_id].items() if data.get("type") == "equiv"]


def map_to_clusters(g: nx.DiGraph, topic_id: UUID) -> set[str]:
    """토픽이 속한 12 cluster label set. 매핑 없으면 빈 set."""
    if topic_id not in g:
        return set()
    labels = g.nodes[topic_id].get("cluster_labels", set())
    return set(labels) if labels else set()


def graph_distance(g: nx.DiGraph, a: UUID, b: UUID) -> int | None:
    """무방향 거리 (BFS). 도달 불가 시 None. cso-mapping.md §3."""
    if a not in g or b not in g:
        return None
    ug = g.to_undirected(as_view=True)
    if not nx.has_path(ug, a, b):
        return None
    result: int = nx.shortest_path_length(ug, a, b)
    return result


def get_children(g: nx.DiGraph, parent_id: UUID) -> Sequence[UUID]:
    """직접 자식 (1-hop). children_count 응답 등에 사용."""
    if parent_id not in g:
        return []
    return list(g.predecessors(parent_id))


def get_parents(g: nx.DiGraph, child_id: UUID) -> Sequence[UUID]:
    """직접 부모 (1-hop). CSOTopicDetail.parents 응답."""
    if child_id not in g:
        return []
    return list(g.successors(child_id))


__all__ = [
    "build_cso_graph",
    "find_adjacent",
    "find_ancestors",
    "find_descendants",
    "find_equivalents",
    "get_children",
    "get_parents",
    "graph_distance",
    "map_to_clusters",
    "verify_cso_import",
]
