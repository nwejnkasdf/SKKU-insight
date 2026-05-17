"""leaf_lifecycle worker — A7 daily emerging 식별 cron 본문.

cron = `LEAF_LIFECYCLE_CRON` (default `30 3 * * *` UTC, COLLECTION_CRON+30분).

흐름 (사용자별):
1. lifespan 의 cso_graph + LLMProvider 가져오기.
2. 최근 24h emerging input 후보 lookup (input D union — A4 collection union UserEvent click/save).
3. HybridDLifecycleEvaluator.identify_emerging() 호출 → Strict 검증 통과 candidate.
4. accepted candidate 들 DB INSERT (DynamicLeafTopic + DynamicLeafTopicCSOTopic).

A7 결정 #14 (collection daily cron 직후 hook) + #15 (anchor retry) + #18 (input D) +
#19 (Strict 검증) 모두 반영.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import networkx as nx
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import EventType, LeafTopicStatus, RedisKey
from app.db.models import (
    DocumentTopic,
    DynamicLeafTopic,
    DynamicLeafTopicCSOTopic,
    User,
    UserEvent,
)
from app.db.session import AsyncSessionLocal
from app.leaf_lifecycle.hybrid_d import HybridDLifecycleEvaluator
from app.leaf_lifecycle.protocol import NewLeafCandidate
from app.llm_provider.protocol import LLMProvider
from app.redis import get_redis
from app.topic.graph import build_cso_graph

logger = logging.getLogger("leaf_lifecycle_job")


async def _collect_input_documents(
    db: AsyncSession,
    user_id: UUID,
) -> list[UUID]:
    """A7 결정 #18 (옵션 D): A4 collection user own Document union UserEvent click/save Document.

    최근 LEAF_EMERGING_INPUT_WINDOW_HOURS (24h) window.
    """
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(
        hours=settings.LEAF_EMERGING_INPUT_WINDOW_HOURS
    )
    # A: user own collection 결과 Document (leaf_topic_id IN user_leaves OR
    #    (cso_topic_id IN user_path AND leaf_topic_id IS NULL))
    # 단순화 1차 시연: user 의 모든 매핑 Document (DocumentTopic.leaf_topic_id 가 user own leaf).
    a_stmt = (
        select(DocumentTopic.document_id)
        .join(
            DynamicLeafTopic,
            DynamicLeafTopic.leaf_topic_id == DocumentTopic.leaf_topic_id,
        )
        .where(
            DynamicLeafTopic.user_id == user_id,
            DynamicLeafTopic.status.in_(
                [LeafTopicStatus.ACTIVE.value, LeafTopicStatus.EMERGING.value]
            ),
        )
        .distinct()
    )
    rows_a = (await db.execute(a_stmt)).scalars().all()
    # C: UserEvent click/save 의 document_id.
    c_stmt = (
        select(UserEvent.document_id)
        .where(
            UserEvent.user_id == user_id,
            UserEvent.event_type.in_([EventType.CLICK.value, EventType.SAVE.value]),
            UserEvent.document_id.isnot(None),
            UserEvent.created_at >= cutoff,
        )
        .distinct()
    )
    rows_c = (await db.execute(c_stmt)).scalars().all()
    # union — set 으로.
    union: set[UUID] = set()
    union.update(rows_a)
    union.update(d for d in rows_c if d is not None)
    return sorted(union)


async def _insert_accepted_candidates(
    db: AsyncSession,
    user_id: UUID,
    active_day_counter: int,
    candidates: list[NewLeafCandidate],
) -> int:
    """accepted NewLeafCandidate 들을 DynamicLeafTopic + M:N 매핑 INSERT.

    A6 C-03 패턴: pg_insert.on_conflict_do_nothing + returning leaf_topic_id.
    """
    now = datetime.now(UTC)
    inserted = 0
    for cand in candidates:
        leaf_id = uuid.uuid4()
        leaf_insert = (
            pg_insert(DynamicLeafTopic)
            .values(
                leaf_topic_id=leaf_id,
                user_id=user_id,
                label=cand.label_ko,
                label_en=cand.label_en,
                confidence=cand.confidence,
                status=LeafTopicStatus.EMERGING.value,
                created_at=now,
                created_active_day=active_day_counter,
                last_signal_active_day=active_day_counter,
                merged_into_leaf_topic_id=None,
            )
            .on_conflict_do_nothing(index_elements=["leaf_topic_id"])
            .returning(DynamicLeafTopic.leaf_topic_id)
        )
        result = await db.execute(leaf_insert)
        new_id = result.scalar_one_or_none()
        if new_id is None:
            continue
        # M:N 매핑 INSERT.
        for cso_id in cand.cso_topic_ids:
            await db.execute(
                pg_insert(DynamicLeafTopicCSOTopic)
                .values(
                    leaf_topic_id=new_id,
                    cso_topic_id=cso_id,
                    confidence=cand.confidence,
                    linked_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["leaf_topic_id", "cso_topic_id"]
                )
            )
        inserted += 1
    return inserted


async def _run_for_user(
    db: AsyncSession,
    redis: aioredis.Redis,
    provider: LLMProvider,
    graph: nx.DiGraph,
    user: User,
) -> int:
    """사용자 1명 처리. lock 보유 → identify_emerging → INSERT.

    return: inserted candidate 수.
    """
    settings = get_settings()
    lock_key = RedisKey.leaf_lifecycle_lock(user.user_id)
    acquired = await redis.set(
        lock_key, "1", nx=True, ex=settings.LEAF_LIFECYCLE_LOCK_TTL_SECONDS
    )
    if not acquired:
        logger.info("leaf_lifecycle skip user=%s (lock held)", user.user_id)
        return 0
    try:
        new_docs = await _collect_input_documents(db, user.user_id)
        if len(new_docs) < settings.LEAF_EMERGING_SUPPORTING_DOCUMENTS_MIN:
            logger.info(
                "leaf_lifecycle skip user=%s (only %d docs, min=%d)",
                user.user_id,
                len(new_docs),
                settings.LEAF_EMERGING_SUPPORTING_DOCUMENTS_MIN,
            )
            return 0
        # existing leaves (user own active) lookup.
        existing_stmt = select(DynamicLeafTopic.leaf_topic_id).where(
            DynamicLeafTopic.user_id == user.user_id,
            DynamicLeafTopic.status == LeafTopicStatus.ACTIVE.value,
        )
        existing_ids = [row[0] for row in await db.execute(existing_stmt)]
        evaluator = HybridDLifecycleEvaluator(db, provider, graph)
        candidates = await evaluator.identify_emerging(
            user.user_id, new_docs, existing_ids
        )
        if not candidates:
            return 0
        inserted = await _insert_accepted_candidates(
            db,
            user.user_id,
            user.active_day_counter or 0,
            candidates[: settings.LEAF_EMERGING_MAX_PER_DAY],
        )
        await db.commit()
        return inserted
    finally:
        await redis.delete(lock_key)


async def _run() -> int:
    """daily cron entry — 모든 사용자 순회."""
    # lifespan 외부에서 호출되므로 graph 재빌드 (worker 환경).
    from app.db.engine import get_engine

    engine = get_engine()
    graph = await build_cso_graph(engine)
    # provider 는 env 기반 토글 (lifespan 과 정합).
    from app.llm_provider import get_provider

    provider = get_provider(get_settings().LLM_PROVIDER)
    redis = get_redis("default")
    total = 0
    async with AsyncSessionLocal() as db:
        users = list(
            (await db.execute(select(User))).scalars().all()
        )
        for user in users:
            try:
                total += await _run_for_user(db, redis, provider, graph, user)
            except Exception:
                logger.exception("leaf_lifecycle_job user=%s failed", user.user_id)
                await db.rollback()
    logger.info("leaf_lifecycle_job total_inserted=%d", total)
    return total


def leaf_lifecycle_job() -> None:
    """RQ sync entrypoint."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


__all__ = ["leaf_lifecycle_job"]
