"""admin 운영 액션 — SUPER 전용 (C-61).

docs/decisions.md §24, docs/api/admin.md §운영 액션.

force_archive_leaf       — DynamicLeafTopic.status='archived' UPDATE
force_archive_trace      — UserCSOTraversal.status='archived' UPDATE (URL = /retract)
cleanup_pseudo_recos     — engine._cleanup_pseudo_recommendations wrapper
enqueue_simulate         — RQ simulate_user_day_job enqueue + Redis status init
get_simulate_status      — Redis lookup
list_system_config       — SystemConfig SELECT 전체
update_system_config     — SystemConfig UPSERT + Redis cache invalidate

NFR-04 score 마스킹 우회는 admin 노출 허용. force_archive_trace 의 URL `/retract` 는
사용자 의도 (강제 종료) 정합. path.pop 정밀 retract 는 본 라운드 범위 밖.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import redis as sync_redis
import redis.asyncio as aioredis
from fastapi import HTTPException, status
from rq import Queue
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import ErrorCode, LeafTopicStatus, RedisKey, TraversalStatus
from app.db.models import AdminUser, DynamicLeafTopic, SystemConfig, UserCSOTraversal
from app.recommendation.engine import _cleanup_pseudo_recommendations

from .schemas import (
    CleanupPseudoResponse,
    SimulateAcceptedResponse,
    SimulateRequest,
    SimulateStatusResponse,
    SystemConfigItem,
    SystemConfigListResponse,
)

_SIMULATE_STATUS_TTL_SECONDS = 3600


def _err(code: ErrorCode, detail: str, status_code: int = 404) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code.value, "message": detail},
    )


async def force_archive_leaf(
    db: AsyncSession,
    redis_async: aioredis.Redis,
    user_id: UUID,
    leaf_id: UUID,
) -> None:
    """leaf status='archived' UPDATE + recommendation cache invalidate."""
    result = await db.execute(
        update(DynamicLeafTopic)
        .where(
            DynamicLeafTopic.leaf_topic_id == leaf_id,
            DynamicLeafTopic.user_id == user_id,
        )
        .values(status=LeafTopicStatus.ARCHIVED.value)
        .returning(DynamicLeafTopic.leaf_topic_id)
    )
    updated = result.scalar_one_or_none()
    if updated is None:
        raise _err(
            ErrorCode.LEAF_TOPIC_NOT_FOUND,
            "leaf 가 존재하지 않거나 해당 사용자 소유가 아닙니다.",
        )
    await db.commit()
    await redis_async.delete(RedisKey.recommendation_cache(user_id))


async def force_archive_trace(
    db: AsyncSession,
    redis_async: aioredis.Redis,
    user_id: UUID,
    trace_id: UUID,
) -> None:
    """trace status='archived' UPDATE + recommendation cache invalidate.

    URL 은 명세 `/retract` 유지 (강제 종료 의도). 실 SQL = archive — path.pop 정밀
    retract 는 admin debug 화면 범위 밖 (LLM leaf_remap 동반이라 무게).
    """
    result = await db.execute(
        update(UserCSOTraversal)
        .where(
            UserCSOTraversal.trace_id == trace_id,
            UserCSOTraversal.user_id == user_id,
        )
        .values(status=TraversalStatus.ARCHIVED.value)
        .returning(UserCSOTraversal.trace_id)
    )
    updated = result.scalar_one_or_none()
    if updated is None:
        raise _err(
            ErrorCode.TOPIC_NOT_FOUND,
            "trace 가 존재하지 않거나 해당 사용자 소유가 아닙니다.",
        )
    await db.commit()
    await redis_async.delete(RedisKey.recommendation_cache(user_id))


async def cleanup_pseudo_recos(
    db: AsyncSession,
    redis_async: aioredis.Redis,
    user_id: UUID,
) -> CleanupPseudoResponse:
    """engine._cleanup_pseudo_recommendations 호출 + commit."""
    deleted = await _cleanup_pseudo_recommendations(db, redis_async, user_id)
    await db.commit()
    return CleanupPseudoResponse(deleted_count=deleted)


def enqueue_simulate(
    admin: AdminUser,
    user_id: UUID,
    req: SimulateRequest,
) -> SimulateAcceptedResponse:
    """RQ simulate_user_day_job enqueue + Redis status key init.

    sync redis 사용 — RQ Queue 가 sync connection 받음. enqueue 직후 status='queued'
    초기화. worker pick 즉시 'running' 으로 덮음 (worker job 내부 _set_status).
    """
    settings = get_settings()
    sync_conn = sync_redis.Redis.from_url(settings.REDIS_URL_QUEUE)
    queue = Queue("default", connection=sync_conn)
    enqueued_at = datetime.now(UTC)
    job = queue.enqueue(
        "app.worker.jobs.simulate_user_day_job.simulate_user_day_job",
        user_id_str=str(user_id),
        mode=req.mode,
        days=req.days,
        job_timeout=3600,
    )
    payload_conn = sync_redis.Redis.from_url(
        settings.REDIS_URL, decode_responses=True
    )
    payload: dict[str, Any] = {
        "state": "queued",
        "mode": req.mode,
        "days_total": 0 if req.mode == "weekly" else req.days,
        "days_done": 0,
        "weekly_chains": 0,
        "started_at": None,
        "finished_at": None,
        "message": None,
    }
    payload_conn.setex(
        RedisKey.simulate_status(user_id),
        _SIMULATE_STATUS_TTL_SECONDS,
        json.dumps(payload, default=str),
    )
    payload_conn.close()
    sync_conn.close()
    _ = admin  # audit 별도 table 없음 — RQ job log 만
    return SimulateAcceptedResponse(
        job_id=job.get_id(), enqueued_at=enqueued_at
    )


async def get_simulate_status(
    redis_async: aioredis.Redis, user_id: UUID
) -> SimulateStatusResponse:
    """Redis simulate:{user_id}:status 직렬화. 없으면 state='idle'."""
    raw = await redis_async.get(RedisKey.simulate_status(user_id))
    if not raw:
        return SimulateStatusResponse(state="idle")
    payload = json.loads(raw if isinstance(raw, str) else raw.decode())
    return SimulateStatusResponse(**payload)


async def list_system_config(db: AsyncSession) -> SystemConfigListResponse:
    """system_config 전체 SELECT."""
    rows = (await db.execute(select(SystemConfig))).scalars().all()
    return SystemConfigListResponse(
        items=[
            SystemConfigItem(
                key=row.key,
                value=row.value,
                description=row.description,
                updated_at=row.updated_at,
                updated_by_admin_id=row.updated_by_admin_id,
            )
            for row in rows
        ]
    )


async def update_system_config(
    db: AsyncSession,
    redis_async: aioredis.Redis,
    admin: AdminUser,
    key: str,
    new_value: dict[str, object],
) -> SystemConfigItem:
    """system_config UPSERT + Redis cache invalidate.

    interest_params / event_weights 는 lifespan + on-demand get_X 가 캐싱. 갱신 후
    즉시 DEL — 다음 read = 신선 DB lookup. 임의 key 도 동일 룰 (생성/갱신 모두 OK).
    """
    stmt = (
        pg_insert(SystemConfig)
        .values(
            key=key,
            value=new_value,
            updated_by_admin_id=admin.admin_id,
        )
        .on_conflict_do_update(
            index_elements=[SystemConfig.key],
            set_={
                "value": new_value,
                "updated_by_admin_id": admin.admin_id,
                "updated_at": datetime.now(UTC),
            },
        )
        .returning(SystemConfig)
    )
    row = (await db.execute(stmt)).scalar_one()
    await db.commit()
    await redis_async.delete(RedisKey.system_config_cache(key))
    return SystemConfigItem(
        key=row.key,
        value=row.value,
        description=row.description,
        updated_at=row.updated_at,
        updated_by_admin_id=row.updated_by_admin_id,
    )


__all__ = [
    "cleanup_pseudo_recos",
    "enqueue_simulate",
    "force_archive_leaf",
    "force_archive_trace",
    "get_simulate_status",
    "list_system_config",
    "update_system_config",
]
