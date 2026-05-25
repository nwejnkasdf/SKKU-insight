"""recommendation service — endpoint 진입점 4종.

- get_dashboard: single-flight Redis lock (concurrency.md §2) + consent cache (§7).
- refresh_dashboard: rate_limit (decorator) + cache delete + get_dashboard(force_refresh).
- get_document_detail: Document + saved/hidden/not_interested flag.
- get_document_summary: summary_service 위임.

§11.#1 (cache-before-commit): db.commit() 성공 후에만 redis.setex(cache).
§11.#5 (lock token race): Lua atomic CAS DEL — 자기 token 일치 시만 release.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid
from datetime import UTC, datetime
from uuid import UUID

import networkx as nx
import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.contracts import ErrorCode, RedisKey
from app.db.models import (
    Document,
    HiddenDocument,
    SavedDocument,
    Source,
    User,
)
from app.interest.config_loader import (
    get_interest_params,
)
from app.llm_provider.protocol import LLMProvider
from app.security.consent_cache import is_consent_active

from .config_loader import RecommendationConfig
from .engine import build_dashboard
from .schemas import (
    DashboardResponse,
    DocumentDetailResponse,
    DocumentSummaryResponse,
)
from .summary_service import get_or_build_summary

logger = logging.getLogger(__name__)


# Lua atomic CAS DEL — 자기 token 일치 시만 lock 해제 (§11.#5).
_RELEASE_LOCK_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] "
    "then return redis.call('DEL', KEYS[1]) end return 0"
)


def _consent_required_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": ErrorCode.RECOMMENDATION_CONSENT_REQUIRED.value,
            "message": "추천 기능은 개인화 동의 활성 후 사용 가능합니다.",
        },
    )


async def _try_load_cache(
    redis: aioredis.Redis, user_id: UUID
) -> DashboardResponse | None:
    """recommendation_cache hit 시 DashboardResponse 반환.

    C-61 후속 (2026-05-25): `collection_in_progress` 는 cache 저장 시점 값이라 stale 가능.
    응답 직전 redis.exists 로 재계산 — UI lock 정합 보장.
    """
    cached_raw = await redis.get(RedisKey.recommendation_cache(user_id))
    if not cached_raw:
        return None
    try:
        resp = DashboardResponse.model_validate_json(cached_raw)
        in_progress = bool(
            await redis.exists(RedisKey.collection_lock(user_id))
        )
        # cache hit 명시 — 저장 시 "miss" 였더라도. collection_in_progress 는 현재 시점.
        return resp.model_copy(
            update={"cache": "hit", "collection_in_progress": in_progress}
        )
    except Exception:
        # corrupt cache — invalidate.
        await redis.delete(RedisKey.recommendation_cache(user_id))
        return None


async def _build_and_cache(
    db: AsyncSession,
    redis: aioredis.Redis,
    provider: LLMProvider,
    cso_graph: nx.DiGraph,
    settings: Settings,
    config: RecommendationConfig,
    user: User,
) -> DashboardResponse:
    """build_dashboard → db.commit() → redis.setex (§11.#1).

    실패 시 rollback + lock 은 caller 가 해제.
    """
    params = await get_interest_params(redis, db)
    result = await build_dashboard(
        db,
        redis,
        provider,
        cso_graph,
        settings,
        params,
        config,
        user=user,
    )
    # cold-start 진행 중 분기는 build_dashboard 가 stored row 로딩 → response 반환.
    # 정상 분기는 INSERT 직후 — db.commit() 호출.
    await db.commit()
    # cache SET — db.commit 성공 후 (§11.#1 cache-before-commit 회피).
    await redis.setex(
        RedisKey.recommendation_cache(user.user_id),
        settings.RECOMMENDATION_CACHE_TTL_SECONDS,
        result.response.model_dump_json(),
    )
    return result.response


async def get_dashboard(
    db: AsyncSession,
    redis: aioredis.Redis,
    provider: LLMProvider,
    cso_graph: nx.DiGraph,
    settings: Settings,
    config: RecommendationConfig,
    user: User,
    *,
    force_refresh: bool = False,
) -> DashboardResponse:
    """single-flight + consent cache + cache hit + 8s polling fallback."""
    # 1. consent active.
    if not await is_consent_active(user.user_id, redis, db):
        raise _consent_required_error()

    # 2. cache hit (force_refresh False 시만).
    if not force_refresh:
        cached = await _try_load_cache(redis, user.user_id)
        if cached is not None:
            return cached

    # 3. single-flight lock — uuid token.
    lock_key = RedisKey.recommendation_build_lock(user.user_id)
    lock_token = _uuid.uuid4().hex
    acquired = await redis.set(
        lock_key,
        lock_token,
        nx=True,
        ex=settings.RECOMMENDATION_BUILD_LOCK_TTL_SECONDS,
    )

    if not acquired:
        # 4. polling — 8s 까지 cache 결과 대기.
        deadline = time.monotonic() + settings.RECOMMENDATION_BUILD_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(settings.RECOMMENDATION_BUILD_POLL_INTERVAL_SECONDS)
            cached = await _try_load_cache(redis, user.user_id)
            if cached is not None:
                return cached
        # 5. polling timeout — 직접 build (lock 만료 가정, no recursive acquire).
        # lock 보유자가 죽었거나 8s 초과 = TTL 30s 안에서도 build 지연. 직접 build 진행.
        logger.warning(
            "recommendation: polling timeout, fallback to direct build user=%s",
            user.user_id,
        )
        return await _build_and_cache(
            db, redis, provider, cso_graph, settings, config, user
        )

    # 6. lock 보유 — build.
    try:
        return await _build_and_cache(
            db, redis, provider, cso_graph, settings, config, user
        )
    except HTTPException:
        # cold-start in progress 등 — caller 가 다룬다. rollback X (read-only path).
        raise
    except Exception:
        await db.rollback()
        raise
    finally:
        # Lua atomic CAS — 자기 token 일치 시만 DEL (§11.#5).
        try:
            await redis.eval(_RELEASE_LOCK_LUA, 1, lock_key, lock_token)  # type: ignore[misc]
        except Exception:
            logger.warning(
                "recommendation: failed to release lock user=%s",
                user.user_id,
            )


async def refresh_dashboard(
    db: AsyncSession,
    redis: aioredis.Redis,
    provider: LLMProvider,
    cso_graph: nx.DiGraph,
    settings: Settings,
    config: RecommendationConfig,
    user: User,
) -> DashboardResponse:
    """rate_limit (decorator) + cache delete + force_refresh build.

    C-61 후속 (2026-05-25): 진행 중 collection_lock 보유 시 409 차단. UI 측 disabled
    button 우회 (devtool / race / stale state) 방어. client 는 `recommendation.
    collection_in_progress` 코드로 banner / toast 안내.
    """
    if not await is_consent_active(user.user_id, redis, db):
        raise _consent_required_error()
    if await redis.exists(RedisKey.collection_lock(user.user_id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": ErrorCode.RECOMMENDATION_COLLECTION_IN_PROGRESS.value,
                "message": "수집 중에는 새로고침할 수 없습니다. 완료 후 다시 시도해주세요.",
            },
        )
    # cache delete (single-flight lock 안에서만 — rate_limit 통과 시).
    await redis.delete(RedisKey.recommendation_cache(user.user_id))
    return await get_dashboard(
        db,
        redis,
        provider,
        cso_graph,
        settings,
        config,
        user,
        force_refresh=True,
    )


async def get_document_detail(
    db: AsyncSession, user: User, document_id: UUID
) -> DocumentDetailResponse:
    """Document fetch + saved/hidden flag. consent 검증은 middleware 책임."""
    stmt = (
        select(
            Document.document_id,
            Document.title,
            Document.url,
            Document.canonical_url,
            Document.published_at,
            Document.summary,
            Source.name.label("source_name"),
            Source.source_type.label("source_type"),
        )
        .join(Source, Source.source_id == Document.source_id)
        .where(Document.document_id == document_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": ErrorCode.DOCUMENT_NOT_FOUND.value,
                "message": "문서를 찾을 수 없습니다.",
            },
        )
    saved_stmt = select(SavedDocument.document_id).where(
        SavedDocument.user_id == user.user_id,
        SavedDocument.document_id == document_id,
    )
    saved = (await db.execute(saved_stmt)).scalar_one_or_none() is not None
    hidden_stmt = select(HiddenDocument.document_id).where(
        HiddenDocument.user_id == user.user_id,
        HiddenDocument.document_id == document_id,
    )
    hidden = (await db.execute(hidden_stmt)).scalar_one_or_none() is not None

    # TopicChip 은 engine._fetch_topic_chips 와 동일 패턴이지만 단일 doc 라 직접 lookup.
    from .engine import (  # local import — circular 회피
        _fetch_not_interested_documents,
        _fetch_topic_chips,
    )

    chips_map = await _fetch_topic_chips(db, [document_id])
    not_interested = document_id in await _fetch_not_interested_documents(
        db, user.user_id, [document_id]
    )
    return DocumentDetailResponse(
        document_id=row.document_id,
        title=row.title,
        source_name=row.source_name,
        source_type=row.source_type,
        url=row.url,
        canonical_url=row.canonical_url,
        published_at=row.published_at or datetime.now(UTC),
        summary_short=(row.summary or "")[:500],
        related_topics=chips_map.get(document_id, []),
        saved=saved,
        hidden=hidden,
        not_interested=not_interested,
    )


async def get_document_summary(
    db: AsyncSession,
    redis: aioredis.Redis,
    provider: LLMProvider,
    settings: Settings,
    document_id: UUID,
) -> DocumentSummaryResponse:
    """summary_service 위임."""
    return await get_or_build_summary(db, redis, provider, settings, document_id)


__all__ = [
    "get_dashboard",
    "get_document_detail",
    "get_document_summary",
    "refresh_dashboard",
]
