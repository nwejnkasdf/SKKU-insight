"""C-53 (2026-05-24) Fusion bridge_cso 결정 알고리즘 — trace↔trace meet in the middle BFS.

사용자 의도 (디자인 논의):
- discovery / adjacent 가 단지 "발견 / 인접" 슬롯이 아니라 **core 확장 통로**
- Fusion = "active trace × archived trace 교차의 새 영역" — 두 영역 사이의 의미적 bridge
- bridge_cso 결정 = LLM 의존 X, 그래프 알고리즘 (deterministic + 재현 가능)

알고리즘:
1. archived_trace.path + active_trace.path 의 각 노드 중 user_interest_state.long_score
   DESC top_k (default 5) 출발점 선택 — path 길이 ≤ top_k 면 전부 사용
2. path 공유 노드 제외 (Fusion = path 밖 새 교차, trivial bridge 회피)
3. 두 path 전체 노드 visited 마킹 (외향 BFS 위해)
4. 외향 frontier 확장 — superTopicOf + relatedEquivalent edge 모두 활용 (양방향 neighbor)
5. 첫 만남 노드 = bridge. tie 시 두 path 거리 sum 최소
6. max_hops (default 3) 안 만나지 않으면 None → caller 가 trend fallback 처리

LCA root 문제 자연 회피: path 위 노드들이 visited 됐으니 root (Computer Science) 도
path 위면 frontier 제외 → 진짜 외부 교차만 bridge 후보.

algorithms/recommendation-ranking.md §Discovery 확장 — Fusion sub-slot 의 source-of-truth
알고리즘. UserProfile generation job (A8-v2 daily 19 UTC) 가 본 함수 호출 → 결과
bridge_cso 를 fusion_candidates 에 저장 → query_discovery_fusion 이 매핑 Document SELECT.
"""
from __future__ import annotations

from uuid import UUID

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserCSOTraversal, UserInterestState


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

    networkx DiGraph 의 successors (child / outgoing) + predecessors (parent / incoming)
    모두 — superTopicOf (계층) + relatedEquivalent (cross-link) 모두 활용.
    """
    result: set[UUID] = set()
    for node in frontier:
        if node in graph:
            result.update(graph.successors(node))
            result.update(graph.predecessors(node))
    return result


def _tie_break(
    meet: set[UUID],
    visited_a: dict[UUID, int],
    visited_b: dict[UUID, int],
) -> UUID:
    """meet 후보 중 두 path 거리 sum 최소 → tie 시 UUID lexicographic.

    sum 최소 = bridge 가 두 영역 사이 가장 균형. tie break (sum 같으면) 는
    deterministic 위해 str(uuid) 정렬.
    """
    return min(
        meet,
        key=lambda n: (visited_a[n] + visited_b[n], str(n)),
    )


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
    """trace↔trace meet in the middle BFS — Fusion bridge_cso 결정.

    Args:
        archived_trace: Reincarnation 으로 선택된 archived trace (status='archived', score_tail >= 0.6)
        active_trace: 신호 가장 강한 active trace (보통 score_tail DESC top 1)
        top_k: 각 path 에서 출발점 선택 (long_score DESC) — path 길이 ≤ top_k 면 전부
        max_hops: 외향 BFS 최대 깊이 — sparse 그래프 (CSO 14k 노드, avg deg ~6) 기준 3 충분

    Returns:
        bridge_cso_topic_id (UUID) — 두 영역의 외부 교차 노드.
        None — meet 실패 (두 영역 멀음 또는 disconnected) → caller 가 fallback.
    """
    # 1. path 의 신호 강한 top_k 출발점 선택.
    top_archived = await _select_top_k_path_nodes(
        db, user_id, list(archived_trace.path), k=top_k
    )
    top_active = await _select_top_k_path_nodes(
        db, user_id, list(active_trace.path), k=top_k
    )
    if not top_archived or not top_active:
        return None

    # 2. path 공유 노드 제외 (Fusion = path 밖 새 교차).
    archived_set = set(archived_trace.path)
    active_set = set(active_trace.path)
    common = archived_set & active_set
    visited_a = {n: 0 for n in archived_set if n not in common}
    visited_b = {n: 0 for n in active_set if n not in common}
    if not visited_a or not visited_b:
        # 두 path 가 완전 공유 → Fusion 무의미.
        return None

    # 3. 출발 frontier = top_k 중 common 제외.
    frontier_a = set(top_archived) - common
    frontier_b = set(top_active) - common
    if not frontier_a or not frontier_b:
        return None

    # 4. 외향 BFS — frontier_a 와 frontier_b 교대 확장, 첫 만남이 bridge.
    for depth in range(1, max_hops + 1):
        # expand a
        next_a = _expand_neighbors(cso_graph, frontier_a) - visited_a.keys()
        for n in next_a:
            visited_a[n] = depth
        meet = next_a & visited_b.keys()
        if meet:
            return _tie_break(meet, visited_a, visited_b)
        # expand b
        next_b = _expand_neighbors(cso_graph, frontier_b) - visited_b.keys()
        for n in next_b:
            visited_b[n] = depth
        meet = next_b & visited_a.keys()
        if meet:
            return _tie_break(meet, visited_a, visited_b)
        frontier_a, frontier_b = next_a, next_b
        if not frontier_a or not frontier_b:
            break
    return None


__all__ = ["find_fusion_bridge"]
