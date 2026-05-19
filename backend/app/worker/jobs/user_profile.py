"""A8-v2 daily user_profile cron worker — 19 UTC 사용자별 캐릭터·fusion seeds 생성.

흐름 (사용자별):
1. user-mutex acquire (`RedisKey.user_profile_generation_lock`, uuid4 token, nx, ex=180s)
2. cold-start 가드 (active + archive 모두 0건 → skip — 기존 trust=high trend rule 유지)
3. fetch_profile_llm_input → ProfileLLMInput
4. generate_profile_payload → UserProfilePayload (None 시 skip)
5. upsert_user_profile → DB INSERT ON CONFLICT DO UPDATE
6. db.commit() — cache-before-commit 회피 (A8 §11 #1 lesson)
7. recommendation_cache 와 user_profile_cache 양쪽 DEL → 다음 dashboard fetch 시 최신 profile
8. per-user try/except + finally Lua atomic CAS release

Anti-pattern 회피 (decisions.md §15 결정 매트릭스):
- cache-before-commit (#1) — commit 후 SETEX/DEL
- read-then-write race (#2) — upsert 가 단일 SQL
- batch IntegrityError (#3) — per-user try/except + commit, 한 사용자 실패가 다른 사용자
  영향 X
- lock release race (#4) — `_RELEASE_LOCK_LUA` 자기 토큰 일치 시만 DEL
- NFR-04 score leakage (#6) — UserProfile JSONB 는 score field 없음 (cso_topic_id 만)
- LLM hallucination (#8) — service.generate_profile_payload 의 bridge_cso 매핑 가드
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
from app.llm_provider import get_provider
from app.profile.config_loader import load_profile_config
from app.profile.service import (
    fetch_profile_llm_input,
    generate_profile_payload,
    upsert_user_profile,
)
from app.redis import get_redis
from app.topic.graph import build_cso_graph

logger = logging.getLogger("user_profile_job")


# Lua atomic CAS release — A7 R2-RG-3 패턴. 자기 token 일치 시만 DEL.
_RELEASE_LOCK_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) end return 0"
)


async def _run() -> int:
    """모든 사용자 순회 — daily 19 UTC cron entry. return: 갱신된 사용자 수."""
    from app.db.engine import get_engine

    db_engine = get_engine()
    graph = await build_cso_graph(db_engine)
    settings = get_settings()
    provider = get_provider(settings.LLM_PROVIDER)
    redis = get_redis("default")
    config = load_profile_config(settings)

    total_updated = 0
    total_skipped_lock = 0
    total_skipped_cold_start = 0
    total_skipped_llm = 0
    total_failed = 0

    async with AsyncSessionLocal() as db:
        users = list((await db.execute(select(User))).scalars().all())
        for user in users:
            lock_key = RedisKey.user_profile_generation_lock(user.user_id)
            lock_token = str(uuid.uuid4())
            acquired = await redis.set(
                lock_key,
                lock_token,
                nx=True,
                ex=config.lock_ttl_seconds,
            )
            if not acquired:
                logger.info(
                    "user_profile skip user=%s (lock held)", user.user_id
                )
                total_skipped_lock += 1
                continue
            committed = False
            try:
                llm_input = await fetch_profile_llm_input(
                    db,
                    user,
                    archive_score_tail_min=config.archive_score_tail_min,
                    input_archive_max=config.input_archive_max,
                    cso_graph=graph,
                )
                if not llm_input.active_traces and not llm_input.archived_traces:
                    logger.info(
                        "user_profile skip user=%s (cold-start: no traces)",
                        user.user_id,
                    )
                    total_skipped_cold_start += 1
                    continue
                payload = await generate_profile_payload(
                    provider,
                    graph,
                    llm_input=llm_input,
                    config=config,
                    user_id=user.user_id,
                )
                if payload is None:
                    logger.warning(
                        "user_profile skip user=%s (LLM generation failed)",
                        user.user_id,
                    )
                    total_skipped_llm += 1
                    continue
                await upsert_user_profile(
                    db,
                    user_id=user.user_id,
                    payload=payload,
                    generator_version=config.generator_version,
                )
                # cache-before-commit 회피 — commit 후 DEL.
                await db.commit()
                committed = True
                total_updated += 1
            except Exception:
                logger.exception(
                    "user_profile user=%s failed", user.user_id
                )
                await db.rollback()
                total_failed += 1
            finally:
                # Codex R1 Suggested #1 fix (2026-05-19): cache invalidate 를 별도
                # try/except 로 분리. redis 실패가 committed DB write 를 rollback
                # 처리하지 않도록. `continue` 후에도 finally 가 실행되어 lock release
                # 가 누락되지 않음 (cold-start / LLM 실패 분기 안전).
                if committed:
                    try:
                        await redis.delete(
                            RedisKey.recommendation_cache(user.user_id)
                        )
                        await redis.delete(
                            RedisKey.user_profile_cache(user.user_id)
                        )
                    except Exception:
                        logger.warning(
                            "user_profile cache invalidate failed user=%s "
                            "(DB committed, cache stale until TTL)",
                            user.user_id,
                        )
                # Lua atomic CAS — 자기 token 일치 시만 DEL.
                try:
                    await redis.eval(  # type: ignore[misc]
                        _RELEASE_LOCK_LUA, 1, lock_key, lock_token
                    )
                except Exception:
                    logger.warning(
                        "user_profile lock release race user=%s",
                        user.user_id,
                    )

    logger.info(
        "user_profile_job updated=%d skipped_lock=%d skipped_cold_start=%d "
        "skipped_llm=%d failed=%d",
        total_updated,
        total_skipped_lock,
        total_skipped_cold_start,
        total_skipped_llm,
        total_failed,
    )
    return total_updated


def user_profile_generation_job() -> None:
    """RQ sync entrypoint."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


__all__ = ["user_profile_generation_job"]
