"""CSO endpoint service — clusters/detail/adjacent/descendants 본문.

NetworkX 메모리 그래프 + DB (BroadInterest·CSOTopic) 조합. clusters 는 24h Redis 캐시.

docs/api/topics.md 비즈니스 룰:
- /topics/cso/clusters: 12 개 정확. Redis 캐시 24h.
- /topics/cso/{id}: 404 if not found. parents list 다중 부모 (cso_topic_parent SOR).
- /topics/cso/{id}/adjacent?hops=1-3: NetworkX find_adjacent.
- /topics/cso/{id}/descendants: NetworkX find_descendants (모든 후손, 큰 응답 가능).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

import networkx as nx
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import CSOTopicSummary, ErrorCode
from app.db.models import BroadInterest, CSOTopic
from app.topic import cache
from app.topic import graph as graph_mod
from app.topic.schemas import (
    AdjacentResponse,
    ClustersResponse,
    CSOCluster,
    CSOTopicDetail,
    DescendantsResponse,
)

logger = logging.getLogger(__name__)

# adjacent / descendants 응답 최대 개수 — 너무 큰 응답 차단 (UI 페이지네이션 부재)
MAX_TOPICS_IN_RESPONSE = 500


def _not_found() -> HTTPException:
    """404 topic.not_found 표준 응답."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": ErrorCode.TOPIC_NOT_FOUND.value,
            "message": "토픽을 찾을 수 없습니다.",
        },
    )


async def get_clusters(
    db: AsyncSession, redis: Redis
) -> ClustersResponse:
    """12 CSO 클러스터 응답. Redis 24h 캐시 hit-first."""
    cached = await cache.get_cluster_cache(redis)
    if cached is not None:
        return ClustersResponse(
            clusters=[CSOCluster.model_validate(c) for c in cached]
        )

    # 캐시 miss — DB 조회 (BroadInterest 12 행 JOIN cso_topic + 토픽별 문서 카운트)
    # 1차 시연: A4 Document 모델 부재 → document_count 0 으로 응답
    stmt = (
        select(
            BroadInterest.broad_interest_id,
            BroadInterest.name,
            BroadInterest.description,
            BroadInterest.cso_cluster_label,
            BroadInterest.cso_seed_topic_id,
            BroadInterest.display_order,
            CSOTopic.label.label("topic_label"),
        )
        .join(CSOTopic, BroadInterest.cso_seed_topic_id == CSOTopic.cso_topic_id)
        .order_by(BroadInterest.display_order)
    )
    rows = await db.execute(stmt)
    clusters: list[CSOCluster] = []
    cache_payload: list[dict[str, object]] = []
    for r in rows:
        cluster = CSOCluster(
            cso_topic_id=r.cso_seed_topic_id,
            label=r.name,
            description_ko=r.description,
            document_count=0,  # A4 Document 부재 — A4 후속에서 채움
        )
        clusters.append(cluster)
        cache_payload.append(
            {
                "cso_topic_id": str(r.cso_seed_topic_id),
                "label": r.name,
                "description_ko": r.description,
                "document_count": 0,
            }
        )

    if clusters:
        await cache.set_cluster_cache(redis, cache_payload)
    return ClustersResponse(clusters=clusters)


async def get_topic_detail(
    db: AsyncSession, g: nx.DiGraph, cso_topic_id: UUID
) -> CSOTopicDetail:
    """CSO 토픽 상세. parents 는 NetworkX 다중 부모 (cso_topic_parent SOR)."""
    stmt = select(
        CSOTopic.cso_topic_id,
        CSOTopic.label,
        CSOTopic.uri,
        CSOTopic.parent_topic_id,
    ).where(CSOTopic.cso_topic_id == cso_topic_id)
    row = (await db.execute(stmt)).first()
    if row is None:
        raise _not_found()

    # NetworkX 에서 다중 부모 list + 자식 카운트
    parent_ids: Sequence[UUID] = (
        graph_mod.get_parents(g, cso_topic_id) if cso_topic_id in g else []
    )
    children = graph_mod.get_children(g, cso_topic_id) if cso_topic_id in g else []

    # parents 의 label 조회 (DB)
    parents: list[CSOTopicSummary] = []
    if parent_ids:
        p_stmt = select(CSOTopic.cso_topic_id, CSOTopic.label).where(
            CSOTopic.cso_topic_id.in_(parent_ids)
        )
        p_rows = await db.execute(p_stmt)
        parents = [
            CSOTopicSummary(cso_topic_id=p.cso_topic_id, label=p.label)
            for p in p_rows
        ]

    return CSOTopicDetail(
        cso_topic_id=row.cso_topic_id,
        label=row.label,
        uri=row.uri,
        parent_topic_id=row.parent_topic_id,
        parents=parents,
        children_count=len(children),
    )


async def get_adjacent(
    db: AsyncSession, g: nx.DiGraph, cso_topic_id: UUID, hops: int
) -> AdjacentResponse:
    """N-hop 인접 토픽. NetworkX find_adjacent. seed 가 그래프에 없으면 404."""
    if cso_topic_id not in g:
        # DB 에는 존재할 수도 있으나 그래프 빌드 후 추가된 노드 — 일관성 위해 404
        exists = await db.execute(
            select(func.count())
            .select_from(CSOTopic)
            .where(CSOTopic.cso_topic_id == cso_topic_id)
        )
        if exists.scalar_one() == 0:
            raise _not_found()
    adjacent_ids = graph_mod.find_adjacent(g, cso_topic_id, hops=hops)[
        :MAX_TOPICS_IN_RESPONSE
    ]
    topics = await _summarize_topics(db, adjacent_ids)
    return AdjacentResponse(seed_id=cso_topic_id, hops=hops, topics=topics)


async def get_descendants(
    db: AsyncSession, g: nx.DiGraph, cso_topic_id: UUID
) -> DescendantsResponse:
    """모든 후손 (자식 방향). 응답 cap 적용 (MAX_TOPICS_IN_RESPONSE)."""
    if cso_topic_id not in g:
        exists = await db.execute(
            select(func.count())
            .select_from(CSOTopic)
            .where(CSOTopic.cso_topic_id == cso_topic_id)
        )
        if exists.scalar_one() == 0:
            raise _not_found()
    desc_ids = graph_mod.find_descendants(g, cso_topic_id)[:MAX_TOPICS_IN_RESPONSE]
    topics = await _summarize_topics(db, desc_ids)
    return DescendantsResponse(seed_id=cso_topic_id, topics=topics)


async def _summarize_topics(
    db: AsyncSession, topic_ids: Sequence[UUID]
) -> list[CSOTopicSummary]:
    """UUID list → CSOTopicSummary list. label 일괄 조회."""
    if not topic_ids:
        return []
    stmt = select(CSOTopic.cso_topic_id, CSOTopic.label).where(
        CSOTopic.cso_topic_id.in_(topic_ids)
    )
    rows = await db.execute(stmt)
    return [CSOTopicSummary(cso_topic_id=r.cso_topic_id, label=r.label) for r in rows]


__all__ = [
    "MAX_TOPICS_IN_RESPONSE",
    "get_adjacent",
    "get_clusters",
    "get_descendants",
    "get_topic_detail",
]
