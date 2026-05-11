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
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
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
    """12 CSO 클러스터 응답. Redis 24h 캐시 hit-first.

    Codex 감사 B-3 fix: cache hit 시 Pydantic ValidationError 발생 시 DEL + DB fallback.
    """
    cached = await cache.get_cluster_cache(redis)
    if cached is not None:
        try:
            return ClustersResponse(
                clusters=[CSOCluster.model_validate(c) for c in cached]
            )
        except ValidationError as e:
            logger.warning(
                "cso clusters cache schema mismatch — invalidate + DB fallback: %s", e
            )
            await cache.invalidate_cluster_cache(redis)

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

    # Codex 감사 B-4 fix: 12 cluster 고정 보장 — len != 12 시 503 fail-fast.
    # CSO 미임포트 (0 행) · 시드 부분 누락 (1-11 행) 모두 비정상. 운영자에게 명확 신호.
    if len(clusters) != 12:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "topic.linkage_error",
                "message": (
                    f"BroadInterest 시드 비정상 ({len(clusters)}/12). "
                    "`make import-cso` 또는 운영자에게 문의."
                ),
            },
        )
    await cache.set_cluster_cache(redis, cache_payload)
    return ClustersResponse(clusters=clusters)


async def get_topic_detail(
    db: AsyncSession, g: nx.DiGraph, cso_topic_id: UUID
) -> CSOTopicDetail:
    """CSO 토픽 상세. parents 는 NetworkX 다중 부모 (cso_topic_parent SOR).

    자체감사 A-4 fix: deprecated `parent_topic_id` 응답 미노출. `parents` list 만.
    """
    stmt = select(
        CSOTopic.cso_topic_id,
        CSOTopic.label,
        CSOTopic.uri,
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
        parents=parents,
        children_count=len(children),
    )


async def get_adjacent(
    db: AsyncSession, g: nx.DiGraph, cso_topic_id: UUID, hops: int
) -> AdjacentResponse:
    """N-hop 인접 토픽. NetworkX find_adjacent.

    자체감사 A-3 fix: graph 부재 시 무조건 404 (DB 존재 여부 무관). graph 가 SOR
    이므로 DB-only 토픽은 stale → 사용자에게 정확한 신호 (404) 가 빈 응답 (200) 보다
    낫다. graph rebuild (CSO 재임포트 + 재시작) 후 정상 응답.
    """
    if cso_topic_id not in g:
        raise _not_found()
    adjacent_ids = graph_mod.find_adjacent(g, cso_topic_id, hops=hops)[
        :MAX_TOPICS_IN_RESPONSE
    ]
    topics = await _summarize_topics(db, adjacent_ids)
    return AdjacentResponse(seed_id=cso_topic_id, hops=hops, topics=topics)


async def get_descendants(
    db: AsyncSession, g: nx.DiGraph, cso_topic_id: UUID
) -> DescendantsResponse:
    """모든 후손 (자식 방향). 응답 cap 적용 (MAX_TOPICS_IN_RESPONSE).

    자체감사 A-3 fix: graph 부재 시 무조건 404 — get_adjacent 와 동일 일관성.
    """
    if cso_topic_id not in g:
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
