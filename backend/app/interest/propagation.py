"""Trace path 조상 1-hop propagation — interest-bayesian.md §propagation + cso-topic-traversal.md §4.

settings.INTEREST_PROPAGATION_ENABLED 가 false (default) 면 본 모듈 함수는 no-op.
A7 (leaf-lifecycle + traversal) 도입 후 true 로 토글 → trace 활성 path 위 조상 노드에
1-hop 0.5 (hop_decay) 감쇠로 가산. trace 외 조상에는 가산 X.

본 구현은 함수 시그니처와 룰 + 단일 노드 모드 (skip) 만 제공. A7 가 본문 활성화 시
별도 PR 로 trace path 위 ancestor lookup 본문 채움.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.contracts import TraversalStatus
from app.db.models import UserCSOTraversal
from app.interest.config_loader import InterestParams

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AncestorPropagation:
    """propagation 결과 — (cso_topic_id, attenuation) 쌍.

    attenuation = hop_decay^hop. 1-hop = 0.5, 2-hop = 0.25, ...
    """

    cso_topic_id: UUID
    attenuation: float


async def compute_ancestor_propagation(
    db: AsyncSession,
    cso_graph: nx.DiGraph,
    settings: Settings,
    params: InterestParams,
    *,
    user_id: UUID,
    leaf_parent_cso_id: UUID,
) -> list[AncestorPropagation]:
    """trace 활성 path 위 조상에 대한 (cso_topic_id, attenuation) 리스트.

    settings.INTEREST_PROPAGATION_ENABLED=false 면 빈 list (단일 노드 모드).

    true 일 때:
    1) UserCSOTraversal active path 들 SELECT
    2) path 안에 leaf_parent_cso_id 포함된 trace 만 대상
    3) 그 trace path 위에서 leaf_parent_cso_id 이후 (=조상 방향) 노드들에 대해
       hop_decay^hop 가산 (max_hops 까지)
    4) trace 외 조상은 제외 (propagation_non_trace_ancestors=false)

    엣지 방향 규약: `g.successors(n)` = 부모 → ancestors 추적은 그래프 X (path 가 SOR).
    path 는 root → leaf 정렬이므로 leaf_parent_cso_id 위치 이후 인덱스 = 조상.
    """
    if not settings.INTEREST_PROPAGATION_ENABLED:
        return []
    rows = (
        await db.execute(
            select(UserCSOTraversal.path).where(
                UserCSOTraversal.user_id == user_id,
                UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
            )
        )
    ).all()
    propagations: dict[UUID, float] = {}
    hop_decay = params.propagation_hop_decay
    max_hops = params.propagation_max_hops
    for row in rows:
        path: list[UUID] = list(row.path or [])
        if leaf_parent_cso_id not in path:
            continue
        idx = path.index(leaf_parent_cso_id)
        # path 가 root → leaf 순서. idx 직전 (조상) 으로 max_hops 까지.
        # 본 노드 자체는 ingest_event_atomic 이 이미 직접 가산했으므로 제외.
        # 본 모듈은 trace 안 조상만 (graph descendants 가 아닌 path 안 노드).
        # idx-1, idx-2, ... 0 인덱스가 조상 방향.
        for hop in range(1, max_hops + 1):
            ancestor_idx = idx - hop
            if ancestor_idx < 0:
                break
            ancestor_id = path[ancestor_idx]
            attenuation = hop_decay**hop
            # 같은 ancestor 가 여러 trace 에서 등장하면 최대 attenuation 채택.
            existing = propagations.get(ancestor_id, 0.0)
            if attenuation > existing:
                propagations[ancestor_id] = attenuation
    return [
        AncestorPropagation(cso_topic_id=cid, attenuation=att)
        for cid, att in propagations.items()
    ]


__all__ = ["AncestorPropagation", "compute_ancestor_propagation"]
