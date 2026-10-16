"""C-53 (2026-05-24) Fusion bridge_cso 결정 + C-73 (2026-06-11) 후보 생성·선택 분리.

사용자 의도 (디자인 논의, C-73 라운드에서 원안 복원):
- discovery / adjacent 가 단지 "발견 / 인접" 슬롯이 아니라 **core 확장 통로**
- Fusion = "active trace x archived trace 교차의 새 영역" — 두 영역 사이의 의미적 bridge
- **원 설계 의도**: 그래프가 두 trace 사이 후보를 결정론적으로 생성하고, LLM 이
  닫힌 후보 목록에서 의미 판단으로 선택 (또는 거부). C-53 은 LLM 단계를 빼고
  min hop-sum 단독 선택으로 구현했는데, 실측 (CSO 3.5, cross-cluster trace 쌍
  400 샘플) 결과 발견 bridge 의 100% 가 깊이 ≤2 범용 허브 (대부분 root) 로
  수렴하는 결함 확인 → C-73 에서 의도 복원.

C-73 알고리즘 (2단 분리):
1. **후보 생성 (deterministic, 본 모듈)** — `find_fusion_bridge_candidates`:
   a. 양 path 의 long_score DESC top_k 노드 교차쌍의 무방향 최단경로 내부 노드
   b. 기존 외향 bidirectional BFS (max_hops) 의 meet 노드 — 첫 만남에서 멈추지
      않고 max_hops 라운드 전체 수집
   c. 합집합에 **깊이 ≥ min_depth 필터** (cluster root = depth 0) — 허브 수렴 차단.
      실측: 깊이 필터 적용 시 발견율 56.5% 무손실 + 구체 토픽 bridge 출현.
   d. (hop_sum ASC, depth DESC, str(uuid)) 정렬 → max_candidates 상한
2. **선택 (LLM, fusion_select_llm 모듈)** — caller (profile/service) 가 후보 라벨을
   LLM 에 제시, LLM 은 선택 또는 **명시적 거부** ({"bridge_cso_topic_id": null}).
   거부/실패 시 fusion_candidates=[] → trend fallback (기존 경로).

LCA root 문제: C-53 의 "path 위 visited 제외" 가드는 root 가 path 에 없으면 무력
(trace path 는 cluster 에서 시작, root 미포함) — min_depth 필터가 실질 가드.

algorithms/recommendation-ranking.md §Discovery 확장 — Fusion sub-slot 의 source-of-truth
알고리즘. UserProfile generation job (A8-v2 daily 19 UTC) 가 본 함수 호출 → 결과
bridge_cso 를 fusion_candidates 에 저장 → query_discovery_fusion 이 매핑 Document SELECT.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserCSOTraversal, UserInterestState

logger = logging.getLogger(__name__)

# 그래프 객체별 깊이 맵 캐시 — 프로세스당 cso_graph 1개가 정상이지만, 테스트의
# toy 그래프 누적을 막기 위해 상한 초과 시 전체 clear.
_DEPTH_CACHE: dict[int, dict[UUID, int]] = {}
_DEPTH_CACHE_MAX = 4


@dataclass(slots=True, frozen=True)
class FusionBridgeCandidate:
    """bridge 후보 1건 — LLM 선택 입력 단위.

    hop_sum: 두 path 출발점들로부터의 거리 합 (작을수록 균형 교차).
    depth: cluster root (in-degree 0... 정확히는 parent 부재 노드) 기준 최소 깊이.
    """

    cso_topic_id: UUID
    hop_sum: int
    depth: int


def _node_depths(graph: nx.DiGraph) -> dict[UUID, int]:
    """모든 노드의 root 기준 최소 깊이 — multi-source BFS.

    app cso_graph 의 엣지 방향은 child → parent ("parent" type, graph.py) 이므로
    root (parent 없는 노드) = out_degree 0, 자식 탐색 = predecessors.
    multi-parent 노드는 BFS 특성상 최소 깊이.
    """
    key = id(graph)
    cached = _DEPTH_CACHE.get(key)
    if cached is not None:
        return cached
    depths: dict[UUID, int] = {
        n: 0 for n in graph.nodes if graph.out_degree(n) == 0
    }
    frontier: list[UUID] = list(depths)
    level = 0
    while frontier:
        level += 1
        next_frontier: list[UUID] = []
        for parent in frontier:
            for child in graph.predecessors(parent):
                if child not in depths:
                    depths[child] = level
                    next_frontier.append(child)
        frontier = next_frontier
    if len(_DEPTH_CACHE) >= _DEPTH_CACHE_MAX:
        _DEPTH_CACHE.clear()
    _DEPTH_CACHE[key] = depths
    return depths


async def _select_top_k_path_nodes(
    db: AsyncSession,
    user_id: UUID,
    path: list[UUID],
    *,
    k: int,
) -> list[UUID]:
    """path 의 cso_topic_id 중 user_interest_state.long_score DESC top_k 반환.

    path 길이 ≤ k 면 전부 반환 (순서는 path 순). state row 없는 노드는 score 0
    취급 (top_k 안 들어감 자연).
    """
    if not path:
        return []
    if len(path) <= k:
        return list(path)
    stmt = (
        select(
            UserInterestState.cso_topic_id,
            UserInterestState.long_score,
        )
        .where(
            UserInterestState.user_id == user_id,
            UserInterestState.cso_topic_id.in_(path),
            UserInterestState.leaf_topic_id.is_(None),
        )
        .order_by(UserInterestState.long_score.desc())
        .limit(k)
    )
    rows = (await db.execute(stmt)).all()
    return [r.cso_topic_id for r in rows]


def _expand_neighbors(graph: nx.DiGraph, frontier: set[UUID]) -> set[UUID]:
    """frontier 의 각 노드의 cso_graph 양방향 이웃 합집합.

    networkx DiGraph 의 successors (parent 방향) + predecessors (child 방향) 모두.
    (주의: app graph 는 hierarchy 엣지만 적재 — relatedEquivalent 는 1차 미사용,
    graph.py §equiv 참조. docstring drift 는 C-73 에서 정정.)
    """
    result: set[UUID] = set()
    for node in frontier:
        if node in graph:
            result.update(graph.successors(node))
            result.update(graph.predecessors(node))
    return result


def _collect_bfs_meets(
    cso_graph: nx.DiGraph,
    *,
    frontier_a: set[UUID],
    frontier_b: set[UUID],
    visited_a: dict[UUID, int],
    visited_b: dict[UUID, int],
    banned: set[UUID],
    max_hops: int,
) -> dict[UUID, int]:
    """외향 bidirectional BFS — max_hops 라운드 전체의 meet 노드 수집.

    C-53 은 첫 meet 에서 즉시 반환 (min hop-sum bias → 허브 수렴). C-73 은
    라운드 전체를 돌며 모든 meet 의 hop_sum 을 모아 caller 가 깊이 필터 후
    랭킹하게 한다.
    """
    meets: dict[UUID, int] = {}
    fa, fb = frontier_a, frontier_b
    for depth_i in range(1, max_hops + 1):
        next_a = _expand_neighbors(cso_graph, fa) - visited_a.keys()
        next_b = _expand_neighbors(cso_graph, fb) - visited_b.keys()
        for n in next_a:
            visited_a[n] = depth_i
        for n in next_b:
            visited_b[n] = depth_i
        for n in (visited_a.keys() & visited_b.keys()) - banned:
            if n not in meets:
                meets[n] = visited_a[n] + visited_b[n]
        fa, fb = next_a, next_b
        if not fa and not fb:
            break
    return meets


def _collect_shortest_path_interiors(
    cso_graph: nx.DiGraph,
    *,
    sources: list[UUID],
    targets: list[UUID],
    banned: set[UUID],
) -> dict[UUID, int]:
    """양 path 노드 교차쌍의 무방향 최단경로 내부 노드 수집 — 원 설계 의도.

    내부 노드의 hop_sum = 해당 경로 길이 (내부 노드는 그 경로 위에서
    dist(a,n)+dist(n,b) = len(sp)-1). 노드별 최소값 유지.
    """
    interiors: dict[UUID, int] = {}
    undirected = cso_graph.to_undirected(as_view=True)
    for a in sources:
        if a not in cso_graph:
            continue
        for b in targets:
            if b not in cso_graph or a == b:
                continue
            try:
                sp = nx.shortest_path(undirected, a, b)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            hop = len(sp) - 1
            for n in sp[1:-1]:
                if n in banned:
                    continue
                prev = interiors.get(n)
                if prev is None or hop < prev:
                    interiors[n] = hop
    return interiors


async def find_fusion_bridge_candidates(
    db: AsyncSession,
    cso_graph: nx.DiGraph,
    user_id: UUID,
    archived_trace: UserCSOTraversal,
    active_trace: UserCSOTraversal,
    *,
    top_k: int = 5,
    max_hops: int = 3,
    min_depth: int = 2,
    max_candidates: int = 8,
) -> list[FusionBridgeCandidate]:
    """Fusion bridge 후보 생성 (C-73) — 최단경로 내부 + BFS meet + 깊이 필터.

    Args:
        archived_trace: Reincarnation softmax 로 선택된 archived trace
        active_trace: softmax 로 선택된 active trace
        top_k: 각 path 에서 출발점 선택 (long_score DESC) — path 길이 ≤ top_k 면 전부
        max_hops: 외향 BFS 최대 깊이
        min_depth: bridge 후보 최소 깊이 (cluster root = 0). 실측 근거: 무필터 시
            min hop-sum 이 root/cluster head 로 100% 수렴.
        max_candidates: LLM 에 제시할 후보 상한.

    Returns:
        (hop_sum ASC, depth DESC, uuid) 정렬 후보 리스트. 빈 리스트 = 후보 부재
        → caller 가 fusion_candidates=[] (trend fallback).
    """
    # 1. path 의 신호 강한 top_k 출발점 선택.
    top_archived = await _select_top_k_path_nodes(
        db, user_id, list(archived_trace.path), k=top_k
    )
    top_active = await _select_top_k_path_nodes(
        db, user_id, list(active_trace.path), k=top_k
    )
    if not top_archived or not top_active:
        return []

    # 2. path 공유 노드 제외 (Fusion = path 밖 새 교차).
    archived_set = set(archived_trace.path)
    active_set = set(active_trace.path)
    banned = archived_set | active_set
    common = archived_set & active_set
    visited_a = {n: 0 for n in archived_set if n not in common}
    visited_b = {n: 0 for n in active_set if n not in common}
    if not visited_a or not visited_b:
        # 두 path 가 완전 공유 → Fusion 무의미.
        return []

    frontier_a = set(top_archived) - common
    frontier_b = set(top_active) - common
    if not frontier_a or not frontier_b:
        return []

    # 3. 후보 수집 — BFS meet (전 라운드) + 최단경로 내부 노드.
    hop_sums = _collect_bfs_meets(
        cso_graph,
        frontier_a=frontier_a,
        frontier_b=frontier_b,
        visited_a=visited_a,
        visited_b=visited_b,
        banned=banned,
        max_hops=max_hops,
    )
    for n, hop in _collect_shortest_path_interiors(
        cso_graph,
        sources=top_archived,
        targets=top_active,
        banned=banned,
    ).items():
        prev = hop_sums.get(n)
        if prev is None or hop < prev:
            hop_sums[n] = hop

    # 4. 깊이 필터 + 랭킹.
    depths = _node_depths(cso_graph)
    candidates = [
        FusionBridgeCandidate(cso_topic_id=n, hop_sum=hs, depth=depths.get(n, 0))
        for n, hs in hop_sums.items()
        if depths.get(n, 0) >= min_depth
    ]
    candidates.sort(key=lambda c: (c.hop_sum, -c.depth, str(c.cso_topic_id)))
    result = candidates[:max_candidates]
    logger.info(
        "fusion_bridge candidates user=%s raw=%d depth_filtered=%d returned=%d",
        user_id,
        len(hop_sums),
        len(candidates),
        len(result),
    )
    return result


async def find_fusion_bridge(
    db: AsyncSession,
    cso_graph: nx.DiGraph,
    user_id: UUID,
    archived_trace: UserCSOTraversal,
    active_trace: UserCSOTraversal,
    *,
    top_k: int = 5,
    max_hops: int = 3,
) -> UUID | None:
    """(C-53 backward-compat wrapper) 후보 1위 단일 반환.

    C-73 이후 production caller 는 `find_fusion_bridge_candidates` + LLM 선택 경로
    사용. 본 wrapper 는 깊이 필터 (min_depth=2 default) 적용 1위 후보를 반환하므로
    C-53 원본과 달리 root/허브로 수렴하지 않는다.
    """
    candidates = await find_fusion_bridge_candidates(
        db,
        cso_graph,
        user_id,
        archived_trace,
        active_trace,
        top_k=top_k,
        max_hops=max_hops,
    )
    return candidates[0].cso_topic_id if candidates else None


__all__ = [
    "FusionBridgeCandidate",
    "find_fusion_bridge",
    "find_fusion_bridge_candidates",
]
