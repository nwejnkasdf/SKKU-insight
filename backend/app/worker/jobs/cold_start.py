"""cold_start worker — A8 (recommendation) Phase 2 후반 본문.

호출 시그니처는 onboarding.service._enqueue_cold_start_job 에서 enqueue 한 그대로:
  (request_id: str, user_id: str, cluster_ids: list[str], user_class: str, locale: str)

RQ sync entrypoint — asyncio.run() 으로 async run_cold_start 호출.

본문 책임 (cold-start.md):
1) Redis HSET status=running → 30% → 60% → 85%
2) LLMProvider 호출 → 10 후보 생성 (5/3/2 분배)
3) validate_cold_start (cold-start.md §응답 검증)
4) sentinel `Source(name='cold_start_pseudo')` source_id 로 pseudo Document INSERT
5) Recommendation x10 + RecommendationSlot x3 rows
6) db.commit() 성공 후 Redis HSET completed (§11.#1 cache-before-commit 회피)
7) 예외 시 rollback + HSET failed + delete onboarding_lock
"""
from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.llm_provider import get_provider
from app.recommendation.cold_start import run_cold_start
from app.redis import get_redis

logger = logging.getLogger("cold_start_job")


async def _run(
    request_id: str,
    user_id: str,
    cluster_ids: list[str],
    user_class: str,
    locale: str,
) -> None:
    settings = get_settings()
    redis: aioredis.Redis = get_redis("default")
    provider = get_provider(settings.LLM_PROVIDER)
    await run_cold_start(
        AsyncSessionLocal,
        redis,
        provider,
        settings,
        request_id=request_id,
        user_id=user_id,
        cluster_ids=cluster_ids,
        user_class=user_class,
        locale=locale,
    )


def cold_start_job(
    request_id: str,
    user_id: str,
    cluster_ids: list[str],
    user_class: str,
    locale: str,
) -> None:
    """RQ entrypoint. onboarding.service 가 enqueue 한 args 그대로."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        _run(request_id, user_id, cluster_ids, user_class, locale)
    )


__all__ = ["cold_start_job"]
