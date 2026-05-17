"""merge_evaluation worker — A7 주간 leaf 병합 cron 본문.

cron = `MERGE_EVALUATION_CRON` (default `0 3 * * 1` UTC = 매주 월 03:00).

trace merge 와 별개. 본 잡은 leaf-topic-lifecycle.md L104-131 의 LLM `evaluate_merges`
를 사용자별로 호출 → primary/merged 결정 → DB 적용 (`status='merged'` + merged_into FK).
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import get_settings
from app.contracts import RedisKey
from app.db.models import User
from app.db.session import AsyncSessionLocal
from app.leaf_lifecycle.leaf_merge_evaluator import (
    evaluate_merges_for_user,
    execute_merges,
)
from app.redis import get_redis

logger = logging.getLogger("merge_evaluation_job")


async def _run() -> int:
    """주간 cron entry — 모든 사용자 순회."""
    from app.llm_provider import get_provider

    provider = get_provider(get_settings().LLM_PROVIDER)
    redis = get_redis("default")
    settings = get_settings()
    total_changed = 0
    async with AsyncSessionLocal() as db:
        users = list((await db.execute(select(User))).scalars().all())
        for user in users:
            lock_key = RedisKey.merge_evaluation_lock(user.user_id)
            acquired = await redis.set(
                lock_key,
                "1",
                nx=True,
                ex=settings.MERGE_EVALUATION_LOCK_TTL_SECONDS,
            )
            if not acquired:
                continue
            try:
                proposals = await evaluate_merges_for_user(
                    db, provider, user.user_id
                )
                if proposals:
                    changed = await execute_merges(db, user.user_id, proposals)
                    total_changed += changed
                    await db.commit()
                    # (Codex R1 Suggested 6) leaf 변경 후 추천 캐시 invalidate.
                    if changed:
                        await redis.delete(
                            RedisKey.recommendation_cache(user.user_id)
                        )
            except Exception:
                logger.exception(
                    "merge_evaluation user=%s failed", user.user_id
                )
                await db.rollback()
            finally:
                await redis.delete(lock_key)
    logger.info("merge_evaluation_job total_changed=%d", total_changed)
    return total_changed


def merge_evaluation_job() -> None:
    """RQ sync entrypoint. scheduler.py 가 등록."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


__all__ = ["merge_evaluation_job"]
