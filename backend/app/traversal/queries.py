"""TraversalEngine read-side (queries) — A6 propagation + A8 추천 의존 SQL.

본 모듈은 자유 함수로 노출되어 DefaultTraversalEngine 의 read 메서드가 wrapper 호출.
별도 protocol 분리 (CQRS) 는 plan 잠정 — A8 진입 시 재확인.

함수 5종 (Protocol §read 참조):
- get_active_traces(db, user_id) — 사용자의 모든 active trace ORM row
- get_current_topics(db, user_id) — 모든 active trace 의 path 끝 노드 (deduplicated)
- get_adjacent_topics(db, graph, user_id) — path 끝의 그래프 1-hop 이웃
- get_descendant_leaves(db, graph, trace, user_id) — trace.path 산하 leaf
- get_emerging_leaves(db, user_id) — 사용자의 모든 emerging leaf

graph 는 NetworkX DiGraph (app.state.cso_graph). caller 가 전달.
"""
from __future__ import annotations

from uuid import UUID

import networkx as nx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import LeafTopicStatus, TraversalStatus
from app.db.models import (
    DynamicLeafTopic,
    DynamicLeafTopicCSOTopic,
    UserCSOTraversal,
)
from app.topic.graph import find_adjacent


async def get_active_traces(
    db: AsyncSession,
    user_id: UUID,
) -> list[UserCSOTraversal]:
    """사용자의 모든 active trace. ORDER BY last_activity_active_day DESC.

    A6 propagation 이 path 위 조상 list 결정 시 호출.
    INTEREST_PROPAGATION_ENABLED=true 토글 후 활성 사용 (PR-3 본문 머지 시점).
    """
    stmt = (
        select(UserCSOTraversal)
        .where(
            UserCSOTraversal.user_id == user_id,
            UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
        )
        .order_by(UserCSOTraversal.last_activity_active_day.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_current_topics(
    db: AsyncSession,
    user_id: UUID,
) -> list[UUID]:
    """모든 active trace 의 path 끝 노드 (current 카테고리 후보).

    deduplicated — 여러 trace 가 같은 말단을 공유하면 1회만 반환.
    cso-topic-traversal.md §6.1 current 정의.
    """
    traces = await get_active_traces(db, user_id)
    seen: set[UUID] = set()
    result: list[UUID] = []
    for trace in traces:
        if not trace.path:
            continue
        tail = trace.path[-1]
        if tail in seen:
            continue
        seen.add(tail)
        result.append(tail)
    return result


async def get_adjacent_topics(
    db: AsyncSession,
    graph: nx.DiGraph,
    user_id: UUID,
    *,
    hops: int = 1,
) -> list[UUID]:
    """path 끝 노드의 그래프 N-hop 이웃 (adjacent 카테고리 후보).

    cso-topic-traversal.md §6.1 adjacent 정의 — current 의 1-hop 이웃 (default).
    current 와 겹치는 노드는 제외 (path 위 노드 자체는 adjacent 아님).
    NetworkX 캐시 (app.state.cso_graph) 사용.
    """
    current = await get_current_topics(db, user_id)
    current_set = set(current)
    seen: set[UUID] = set()
    result: list[UUID] = []
    for tail in current:
        try:
            neighbors = find_adjacent(graph, tail, hops=hops)
        except Exception:
            continue
        for n in neighbors:
            if n in current_set or n in seen:
                continue
            seen.add(n)
            result.append(n)
    return result


async def get_descendant_leaves(
    db: AsyncSession,
    user_id: UUID,
    *,
    trace: UserCSOTraversal | None = None,
    trace_id: UUID | None = None,
) -> list[DynamicLeafTopic]:
    """trace.path 산하 cso_topic 매핑 leaf list (active + emerging, merged/archived 제외).

    `trace` 또는 `trace_id` 둘 중 하나 전달. trace_id 만 주면 본 함수가 ORM lookup.
    결정 #16 — merged/archived 는 추천에서 자동 제외.

    의사 SQL:
    ```
    SELECT leaf.* FROM dynamic_leaf_topic AS leaf
    JOIN dynamic_leaf_topic_cso_topic AS m
      ON m.leaf_topic_id = leaf.leaf_topic_id
    WHERE leaf.user_id = :user_id
      AND leaf.status IN ('active','emerging')
      AND m.cso_topic_id = ANY(:trace.path)
    GROUP BY leaf.leaf_topic_id
    ```
    """
    if trace is None and trace_id is None:
        raise ValueError("trace 또는 trace_id 중 하나는 전달해야 함")
    if trace is None:
        assert trace_id is not None
        stmt = select(UserCSOTraversal).where(
            UserCSOTraversal.trace_id == trace_id,
            UserCSOTraversal.user_id == user_id,
        )
        trace = (await db.execute(stmt)).scalar_one_or_none()
        if trace is None:
            return []
    if not trace.path:
        return []
    leaf_stmt = (
        select(DynamicLeafTopic)
        .join(
            DynamicLeafTopicCSOTopic,
            DynamicLeafTopicCSOTopic.leaf_topic_id == DynamicLeafTopic.leaf_topic_id,
        )
        .where(
            DynamicLeafTopic.user_id == user_id,
            DynamicLeafTopic.status.in_(
                [LeafTopicStatus.ACTIVE.value, LeafTopicStatus.EMERGING.value]
            ),
            DynamicLeafTopicCSOTopic.cso_topic_id.in_(trace.path),
        )
        .distinct()
    )
    return list((await db.execute(leaf_stmt)).scalars().all())


async def get_emerging_leaves(
    db: AsyncSession,
    user_id: UUID,
) -> list[DynamicLeafTopic]:
    """사용자의 모든 emerging leaf (core 슬롯 emerging quota 후보).

    decisions.md §4 — core 5 중 1개 emerging quota. last_signal_active_day DESC 정렬.
    """
    stmt = (
        select(DynamicLeafTopic)
        .where(
            DynamicLeafTopic.user_id == user_id,
            DynamicLeafTopic.status == LeafTopicStatus.EMERGING.value,
        )
        .order_by(DynamicLeafTopic.last_signal_active_day.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def find_active_trace_matching(
    db: AsyncSession,
    user_id: UUID,
    cso_topic_id: UUID,
) -> UserCSOTraversal | None:
    """`cso_topic_id` 가 path 위 어딘가에 있는 active trace 반환 (없으면 None).

    GIN index ix_user_cso_traversal_path_gin 활용. 다중 matching 시 가장 최근 활동
    trace 반환 (`last_activity_active_day DESC LIMIT 1`).
    """
    stmt = (
        select(UserCSOTraversal)
        .where(
            UserCSOTraversal.user_id == user_id,
            UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
            # ARRAY contains: path @> ARRAY[cso_topic_id]
            UserCSOTraversal.path.contains([cso_topic_id]),
        )
        .order_by(UserCSOTraversal.last_activity_active_day.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def count_active_traces(db: AsyncSession, user_id: UUID) -> int:
    """사용자의 active trace 수 (TRACE_ACTIVE_CAP 검사용)."""
    from sqlalchemy import func as sa_func

    stmt = select(sa_func.count(UserCSOTraversal.trace_id)).where(
        UserCSOTraversal.user_id == user_id,
        UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
    )
    result = await db.execute(stmt)
    count = result.scalar_one()
    return int(count or 0)


# ============================================================
# A8-v2 (UserProfile + Discovery Fusion + Reincarnation, 2026-05-19)
# ============================================================


async def get_archived_traces_with_score(
    db: AsyncSession,
    user_id: UUID,
    *,
    score_tail_min: float,
    limit: int,
) -> list[UserCSOTraversal]:
    """사용자의 archived trace 중 `score_tail >= score_tail_min` 만 반환.

    A8-v2 daily user_profile cron 의 LLM input 풀. 강한 신호로 종료된 archive 만 fusion /
    reincarnation 의 source. 자연 둔화 archive (score_tail < 임계) 는 노이즈로 제외.

    정렬 — score_tail DESC, archive 시점 DESC. limit 적용 (token 폭주 가드).
    `merged_into_trace_id IS NULL` 인 archive 만 (winner 로 흡수된 loser 는 제외).

    (C-44 P2-27 fix, 2026-05-19) 정렬 키가 `COALESCE(archived_at_active_day,
    last_activity_active_day)` — 신규 archive 는 archived_at, alembic 0008 이전
    row 는 last_activity fallback (backward-compat).
    """
    archive_sort_key = func.coalesce(
        UserCSOTraversal.archived_at_active_day,
        UserCSOTraversal.last_activity_active_day,
    )
    stmt = (
        select(UserCSOTraversal)
        .where(
            UserCSOTraversal.user_id == user_id,
            UserCSOTraversal.status == TraversalStatus.ARCHIVED.value,
            UserCSOTraversal.score_tail >= score_tail_min,
            UserCSOTraversal.merged_into_trace_id.is_(None),
        )
        .order_by(
            UserCSOTraversal.score_tail.desc(),
            archive_sort_key.desc(),
        )
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_top_archived_trace(
    db: AsyncSession,
    user_id: UUID,
    *,
    score_tail_min: float,
    gap_days_min: int,
    current_active_day: int,
) -> UserCSOTraversal | None:
    """discovery reincarnation 후보 — score_tail >= 임계 + archive 시점이 gap_days 전.

    PR-5 (recommendation engine) 가 discovery slot 2 reincarnation 분기에서 호출.
    가장 강한 신호 (score_tail DESC) + 충분한 시간 지난 (gap_days_min) archive 1건.
    `merged_into_trace_id IS NULL` 강제.

    (C-44 P2-27 fix, 2026-05-19) gap_days 비교가 `COALESCE(archived_at_active_day,
    last_activity_active_day) <= cutoff` — fix 전: last_activity 만 비교 (archive
    직후 동일 값이라 gap_days 의미 약화). fix 후: 신규 archive 는 archived_at 기준,
    옛 archive 는 fallback. A8-v2 P2-27 회복.
    """
    cutoff_active_day = current_active_day - gap_days_min
    archive_sort_key = func.coalesce(
        UserCSOTraversal.archived_at_active_day,
        UserCSOTraversal.last_activity_active_day,
    )
    stmt = (
        select(UserCSOTraversal)
        .where(
            UserCSOTraversal.user_id == user_id,
            UserCSOTraversal.status == TraversalStatus.ARCHIVED.value,
            UserCSOTraversal.score_tail >= score_tail_min,
            UserCSOTraversal.merged_into_trace_id.is_(None),
            archive_sort_key <= cutoff_active_day,
        )
        .order_by(
            UserCSOTraversal.score_tail.desc(),
            archive_sort_key.desc(),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_descendant_archived_leaves(
    db: AsyncSession,
    user_id: UUID,
    *,
    trace: UserCSOTraversal,
) -> list[DynamicLeafTopic]:
    """archived trace.path 산하 archived/merged leaf list — PR-5 reincarnation 후보 풀.

    `get_descendant_leaves` 와 대칭 — 단 status 가 archived/merged (자연 망각된 leaf).
    LeafTopicStatus.ARCHIVED + MERGED 양쪽 포함 (둘 다 사용자 본인 흥미가 있었던 영역).
    """
    if not trace.path:
        return []
    stmt = (
        select(DynamicLeafTopic)
        .join(
            DynamicLeafTopicCSOTopic,
            DynamicLeafTopicCSOTopic.leaf_topic_id == DynamicLeafTopic.leaf_topic_id,
        )
        .where(
            DynamicLeafTopic.user_id == user_id,
            DynamicLeafTopic.status.in_(
                [LeafTopicStatus.ARCHIVED.value, LeafTopicStatus.MERGED.value]
            ),
            DynamicLeafTopicCSOTopic.cso_topic_id.in_(trace.path),
        )
        .distinct()
    )
    return list((await db.execute(stmt)).scalars().all())


__all__ = [
    "count_active_traces",
    "find_active_trace_matching",
    "get_active_traces",
    "get_adjacent_topics",
    "get_archived_traces_with_score",
    "get_current_topics",
    "get_descendant_archived_leaves",
    "get_descendant_leaves",
    "get_emerging_leaves",
    "get_top_archived_trace",
]
