"""trace_merge worker — A7 daily trace merge cron 본문.

cron = `TRACE_MERGE_CRON` (default `0 18 * * *` UTC = 03:00 KST, A6 INTEREST_DECAY 와 동시각).

흐름 (사용자별):
1. lock 획득 (trace_merge_lock(user_id), TTL 120s).
2. DefaultTraversalEngine.evaluate_merge_candidates(user_id) 호출.
   → 룰 trigger (path overlap ≥ 3 또는 proper subset) + LLM 검증 + execute_merge.

A7 결정 #17 (merge operation 신규) + #21 (룰+LLM 결합) + #22 (winner 결정) + #23 (daily 18 UTC).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.contracts import RedisKey
from app.db.models import User
from app.db.session import AsyncSessionLocal
from app.redis import get_redis
from app.traversal.default import DefaultTraversalEngine

logger = logging.getLogger("trace_merge_job")


# (Codex R2-RG-3 fix) lock release Lua atomic — 자기 token 일치 시만 DEL.
_RELEASE_LOCK_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
)


async def _run() -> int:
    """모든 사용자 순회 — daily 18 UTC cron entry."""
    from app.db.engine import get_engine
    from app.llm_provider import get_provider
    from app.topic.graph import build_cso_graph

    engine = get_engine()
    graph = await build_cso_graph(engine)
    provider = get_provider(get_settings().LLM_PROVIDER)
    redis = get_redis("default")
    total_merged = 0
    async with AsyncSessionLocal() as db:
        users = list((await db.execute(select(User))).scalars().all())
        for user in users:
            # (Codex R2-RG-1 fix) lock key 통일 — daily_lifecycle_evaluation 와
            # trace_merge 둘 다 trace mutation 이므로 같은 user-mutex 사용해야 race 차단.
            # TRACE_MERGE_LOCK_TTL_SECONDS=120s 가 LLM 호출 동반이라 TTL 길게.
            # (R2-RG-3) lock value = uuid4 토큰 — release 시 Lua atomic CAS.
            lock_key = RedisKey.traversal_lock(user.user_id)
            settings = get_settings()
            lock_token = str(uuid.uuid4())
            acquired = await redis.set(
                lock_key,
                lock_token,
                nx=True,
                ex=settings.TRACE_MERGE_LOCK_TTL_SECONDS,
            )
            if not acquired:
                continue
            try:
                engine_instance = DefaultTraversalEngine(db, provider, graph)
                plans = await engine_instance.evaluate_merge_candidates(
                    user.user_id
                )
                total_merged += len(plans)
                if plans:
                    await db.commit()
                    # (Codex R1 Suggested 6) trace 변경 후 추천 캐시 invalidate.
                    await redis.delete(
                        RedisKey.recommendation_cache(user.user_id)
                    )
            except Exception:
                logger.exception(
                    "trace_merge_job user=%s failed", user.user_id
                )
                await db.rollback()
            finally:
                # R2-RG-3 fix: Lua atomic — 자기 token 일치 시만 DEL.
                try:
                    await redis.eval(  # type: ignore[misc]
                        _RELEASE_LOCK_LUA, 1, lock_key, lock_token
                    )
                except Exception:
                    logger.warning(
                        "trace_merge_job lock release race user=%s",
                        user.user_id,
                    )
    logger.info("trace_merge_job total_merged=%d", total_merged)
    return total_merged


def trace_merge_job() -> None:
    """RQ sync entrypoint."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


__all__ = ["trace_merge_job"]
