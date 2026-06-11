"""C-73 fusion bridge 후보 생성 테스트 — 허브 수렴 차단 + 최단경로 후보.

toy 그래프 (app cso_graph 컨벤션: 엣지 방향 child → parent):

        cs (root, depth 0)
       /  \\
     ra    rb            (cluster heads, depth 1)
     |      |
     a1    b1            (depth 2)
     |      |
     a2    b2            (depth 3)
       \\  /
        w                (depth 4 — 양쪽 모두의 자식 = 진성 교차 토픽)

archived path = [ra, a1, a2] / active path = [rb, b1, b2].
C-53 min hop-sum 단독 선택이라면 cs (hop_sum 2·2+2... ra→cs 1 + rb→cs 1 = 2) 와
w (a2→w 1 + b2→w 1 = 2) 가 tie — UUID 순서에 따라 cs 가 뽑힐 수 있는 구조.
C-73 깊이 필터 (min_depth=2) 는 cs (depth 0) 를 차단하고 w 만 남긴다.

DB 미사용: path 길이 3 ≤ top_k 5 → `_select_top_k_path_nodes` 가 db.execute 도달
전에 early return — db 인자는 dummy 로 충분 (no DB / LLM).
"""
from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import MagicMock

import networkx as nx
from sqlalchemy.ext.asyncio import AsyncSession

from app.traversal.fusion_bridge import (
    FusionBridgeCandidate,
    find_fusion_bridge,
    find_fusion_bridge_candidates,
)


def _trace(path: list[uuid.UUID]) -> MagicMock:
    trace = MagicMock()
    trace.trace_id = uuid.uuid4()
    trace.path = path
    return trace


def _dummy_db() -> AsyncSession:
    return cast(AsyncSession, object())


def _toy_graph() -> tuple[nx.DiGraph, dict[str, uuid.UUID]]:
    """docstring 의 toy 그래프. 반환: (graph, 이름→UUID 맵)."""
    names = ["cs", "ra", "rb", "a1", "a2", "b1", "b2", "w"]
    ids = {n: uuid.uuid4() for n in names}
    g: nx.DiGraph = nx.DiGraph()
    for n in names:
        g.add_node(ids[n], label=n)
    # child → parent (app graph.py 컨벤션)
    edges = [
        ("ra", "cs"),
        ("rb", "cs"),
        ("a1", "ra"),
        ("a2", "a1"),
        ("b1", "rb"),
        ("b2", "b1"),
        ("w", "a2"),
        ("w", "b2"),
    ]
    for child, parent in edges:
        g.add_edge(ids[child], ids[parent], type="parent")
    return g, ids


class TestDepthFilterBlocksHubs:
    async def test_root_hub_filtered_specific_bridge_returned(self) -> None:
        """min_depth=2 가 cs (depth 0) 차단 — w (depth 4) 만 후보."""
        g, ids = _toy_graph()
        archived = _trace([ids["ra"], ids["a1"], ids["a2"]])
        active = _trace([ids["rb"], ids["b1"], ids["b2"]])
        candidates = await find_fusion_bridge_candidates(
            _dummy_db(), g, uuid.uuid4(), archived, active, min_depth=2
        )
        ids_returned = {c.cso_topic_id for c in candidates}
        assert ids["cs"] not in ids_returned, "root 허브가 후보에 들어옴 — 깊이 필터 실패"
        assert ids["w"] in ids_returned, "진성 교차 노드 w 가 후보에 없음"

    async def test_min_depth_zero_reproduces_hub_candidate(self) -> None:
        """min_depth=0 (C-53 등가) 이면 cs 허브가 후보에 들어옴 — 필터 효과의 대조군."""
        g, ids = _toy_graph()
        archived = _trace([ids["ra"], ids["a1"], ids["a2"]])
        active = _trace([ids["rb"], ids["b1"], ids["b2"]])
        candidates = await find_fusion_bridge_candidates(
            _dummy_db(), g, uuid.uuid4(), archived, active, min_depth=0
        )
        ids_returned = {c.cso_topic_id for c in candidates}
        assert ids["cs"] in ids_returned

    async def test_path_nodes_never_candidates(self) -> None:
        """양 path 위 노드는 bridge 후보에서 제외 (Fusion = path 밖 새 교차)."""
        g, ids = _toy_graph()
        archived = _trace([ids["ra"], ids["a1"], ids["a2"]])
        active = _trace([ids["rb"], ids["b1"], ids["b2"]])
        candidates = await find_fusion_bridge_candidates(
            _dummy_db(), g, uuid.uuid4(), archived, active, min_depth=0
        )
        path_ids = {ids[n] for n in ("ra", "a1", "a2", "rb", "b1", "b2")}
        assert not path_ids & {c.cso_topic_id for c in candidates}


class TestShortestPathInteriors:
    async def test_interior_node_found_beyond_bfs_hops(self) -> None:
        """BFS max_hops 밖이어도 최단경로 내부 노드는 후보로 수집.

        a2 와 b2 사이를 사다리 (w1-w2-w3, 길이 4 — cs 경유 길이 6 보다 strict 하게
        짧음) 로 연결한 그래프 — max_hops=1 BFS 로는 meet 불가 (a2→w1, b2→w3 만
        도달), 최단경로 내부 수집이 후보를 공급.
        """
        names = ["cs", "ra", "rb", "a1", "a2", "b1", "b2", "w1", "w2", "w3"]
        ids = {n: uuid.uuid4() for n in names}
        g: nx.DiGraph = nx.DiGraph()
        for n in names:
            g.add_node(ids[n], label=n)
        edges = [
            ("ra", "cs"), ("rb", "cs"),
            ("a1", "ra"), ("a2", "a1"),
            ("b1", "rb"), ("b2", "b1"),
            # 사다리: a2 ← w1 ← w2 ← w3, w3 은 b2 의 자식이기도
            ("w1", "a2"), ("w2", "w1"), ("w3", "w2"), ("w3", "b2"),
        ]
        for child, parent in edges:
            g.add_edge(ids[child], ids[parent], type="parent")
        archived = _trace([ids["ra"], ids["a1"], ids["a2"]])
        active = _trace([ids["rb"], ids["b1"], ids["b2"]])
        candidates = await find_fusion_bridge_candidates(
            _dummy_db(), g, uuid.uuid4(), archived, active,
            max_hops=1, min_depth=2,
        )
        returned = {c.cso_topic_id for c in candidates}
        # 사다리 내부 노드 (w1 은 a2 의 자식 = depth 4 — min_depth 통과) 수집 확인
        ladder = {ids[n] for n in ("w1", "w2", "w3")}
        assert returned & ladder, "최단경로 내부 노드가 후보에 없음"

    async def test_ranking_deterministic_and_capped(self) -> None:
        g, ids = _toy_graph()
        archived = _trace([ids["ra"], ids["a1"], ids["a2"]])
        active = _trace([ids["rb"], ids["b1"], ids["b2"]])
        run1 = await find_fusion_bridge_candidates(
            _dummy_db(), g, uuid.uuid4(), archived, active,
            min_depth=0, max_candidates=2,
        )
        run2 = await find_fusion_bridge_candidates(
            _dummy_db(), g, uuid.uuid4(), archived, active,
            min_depth=0, max_candidates=2,
        )
        assert len(run1) <= 2
        assert [c.cso_topic_id for c in run1] == [c.cso_topic_id for c in run2]
        # 정렬 키 검증: hop_sum ASC, 같으면 depth DESC
        keys = [(c.hop_sum, -c.depth) for c in run1]
        assert keys == sorted(keys)


class TestEmptyAndDegenerate:
    async def test_disconnected_clusters_return_empty(self) -> None:
        """연결 안 된 두 클러스터 — 후보 없음 → [] (caller trend fallback)."""
        ids = {n: uuid.uuid4() for n in ("ra", "a1", "rb", "b1")}
        g: nx.DiGraph = nx.DiGraph()
        for n, nid in ids.items():
            g.add_node(nid, label=n)
        g.add_edge(ids["a1"], ids["ra"], type="parent")
        g.add_edge(ids["b1"], ids["rb"], type="parent")
        archived = _trace([ids["ra"], ids["a1"]])
        active = _trace([ids["rb"], ids["b1"]])
        candidates = await find_fusion_bridge_candidates(
            _dummy_db(), g, uuid.uuid4(), archived, active, min_depth=0
        )
        assert candidates == []

    async def test_fully_shared_paths_return_empty(self) -> None:
        g, ids = _toy_graph()
        same = _trace([ids["ra"], ids["a1"], ids["a2"]])
        same2 = _trace([ids["ra"], ids["a1"], ids["a2"]])
        assert (
            await find_fusion_bridge_candidates(
                _dummy_db(), g, uuid.uuid4(), same, same2
            )
            == []
        )

    async def test_empty_path_returns_empty(self) -> None:
        g, ids = _toy_graph()
        archived = _trace([])
        active = _trace([ids["rb"], ids["b1"], ids["b2"]])
        assert (
            await find_fusion_bridge_candidates(
                _dummy_db(), g, uuid.uuid4(), archived, active
            )
            == []
        )


class TestBackwardCompatWrapper:
    async def test_wrapper_returns_top_candidate(self) -> None:
        """find_fusion_bridge wrapper — 깊이 필터 1위 (w) 반환, root 아님."""
        g, ids = _toy_graph()
        archived = _trace([ids["ra"], ids["a1"], ids["a2"]])
        active = _trace([ids["rb"], ids["b1"], ids["b2"]])
        bridge = await find_fusion_bridge(
            _dummy_db(), g, uuid.uuid4(), archived, active
        )
        assert bridge == ids["w"]

    async def test_wrapper_none_when_no_candidates(self) -> None:
        ids = {n: uuid.uuid4() for n in ("ra", "a1", "rb", "b1")}
        g: nx.DiGraph = nx.DiGraph()
        for n, nid in ids.items():
            g.add_node(nid, label=n)
        g.add_edge(ids["a1"], ids["ra"], type="parent")
        g.add_edge(ids["b1"], ids["rb"], type="parent")
        archived = _trace([ids["ra"], ids["a1"]])
        active = _trace([ids["rb"], ids["b1"]])
        assert (
            await find_fusion_bridge(_dummy_db(), g, uuid.uuid4(), archived, active)
            is None
        )


class TestCandidateDataclass:
    def test_fields(self) -> None:
        cand: Any = FusionBridgeCandidate(
            cso_topic_id=uuid.uuid4(), hop_sum=2, depth=4
        )
        assert cand.hop_sum == 2
        assert cand.depth == 4
