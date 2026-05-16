"""collection 서비스 — router thin, 비즈니스 로직 본 모듈.

docs/api/collection.md 비즈니스 룰:
- GET /collection/jobs/me: 7일 윈도우, cursor pagination (default 20/max 100), latest + history.
- POST /collection/jobs/me/run-now: lock 충돌 409, rate 한도 429, RQ enqueue + jitter eta 반환.
- NFR-08: PublicView (failure_reason 마스킹) 사용.

(v13 round 2 Codex fix, 2026-05-16):
- S-01: `trigger_run_now` 가 CollectionJob `queued` row 를 먼저 INSERT 후 그 job_id 를
  worker 에 전달 → 응답 job_id 와 worker 가 만드는 row job_id 정합.
- S-02: 별도 enqueue lock (TTL 10s) — long-running collection_lock 과 분리. cron+manual
  race 차단.
- S-05: `get_my_jobs` 가 next_cursor / has_more 응답에 포함.
- S-06: contracts.JobType enum 사용 (inline Literal 폐기).
- N-01: type:ignore 제거 (JobType enum 으로 자연 해소).
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.orchestrator import (
    LLM_SEARCH_SENTINEL_NAME,
    deterministic_jitter_seconds,
)
from app.collection.schemas import (
    CollectionJobMeResponse,
    CollectionJobPublicView,
    RunNowResponse,
)
from app.config import get_settings
from app.contracts import CollectionJobStatus, ErrorCode, JobType, RedisKey
from app.db.models import CollectionJob, Source

_HISTORY_WINDOW_DAYS = 7
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_ENQUEUE_LOCK_TTL_SECONDS = 10


def _encode_cursor(started_at: datetime, job_id: UUID) -> str:
    payload = json.dumps(
        {"ts": started_at.isoformat(), "id": str(job_id)}
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError(f"cursor payload must be dict, got {type(data).__name__}")
        ts_val = data["ts"]
        id_val = data["id"]
        if not isinstance(ts_val, str) or not isinstance(id_val, str):
            raise TypeError("cursor fields ts/id must be str")
        return datetime.fromisoformat(ts_val), UUID(id_val)
    except (binascii.Error, TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": f"잘못된 cursor: {e}",
            },
        ) from e


def _to_public_view(row: CollectionJob) -> CollectionJobPublicView:
    """NFR-08 — failure_reason 마스킹 (스키마에 필드 없음)."""
    # user_id 가 nullable 이지만 사용자 응답 컨텍스트에선 항상 채워짐
    assert row.user_id is not None, "collection job for user response must have user_id"
    return CollectionJobPublicView(
        job_id=row.job_id,
        user_id=row.user_id,
        source_id=row.source_id,
        target_cso_topic_id=row.target_cso_topic_id,
        target_leaf_topic_id=row.target_leaf_topic_id,
        job_type=JobType(row.job_type),
        status=CollectionJobStatus(row.status),
        retry_count=row.retry_count,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


async def get_my_jobs(
    db: AsyncSession,
    user_id: UUID,
    cursor: str | None,
    limit: int,
) -> CollectionJobMeResponse:
    """latest + history (7d window) 응답. cursor pagination (S-05)."""
    limit = max(1, min(limit, _MAX_LIMIT))
    cutoff = datetime.now(UTC) - timedelta(days=_HISTORY_WINDOW_DAYS)

    # latest 는 윈도우와 무관하게 가장 최근 1건 (cursor 없을 때만 의미)
    latest: CollectionJobPublicView | None = None
    if not cursor:
        latest_stmt = (
            select(CollectionJob)
            .where(CollectionJob.user_id == user_id)
            .order_by(desc(CollectionJob.started_at), desc(CollectionJob.job_id))
            .limit(1)
        )
        latest_row = (await db.execute(latest_stmt)).scalar_one_or_none()
        latest = _to_public_view(latest_row) if latest_row else None

    history_stmt = (
        select(CollectionJob)
        .where(
            CollectionJob.user_id == user_id,
            CollectionJob.started_at >= cutoff,
        )
        .order_by(desc(CollectionJob.started_at), desc(CollectionJob.job_id))
    )
    if cursor:
        ts, jid = _decode_cursor(cursor)
        history_stmt = history_stmt.where(
            (CollectionJob.started_at < ts)
            | (
                (CollectionJob.started_at == ts)
                & (CollectionJob.job_id < jid)
            )
        )
    history_stmt = history_stmt.limit(limit + 1)
    history_rows = list((await db.execute(history_stmt)).scalars().all())
    has_more = len(history_rows) > limit
    page = history_rows[:limit]
    history = [_to_public_view(r) for r in page]
    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        # started_at 은 row 가 queued 시점에 None 일 수 있음 → created_at fallback
        cursor_ts = last.started_at if last.started_at else last.created_at
        next_cursor = _encode_cursor(cursor_ts, last.job_id)
    return CollectionJobMeResponse(
        latest=latest,
        history=history,
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def trigger_run_now(
    db: AsyncSession,
    redis: aioredis.Redis,
    user_id: UUID,
) -> RunNowResponse:
    """run-now 트리거. S-01·S-02 fix:

    1. enqueue lock (TTL 10s) 획득 — cron+manual race 차단
    2. long-running collection_lock 존재 확인 → 409
    3. CollectionJob `queued` row 를 DB 에 먼저 INSERT (응답 job_id 와 worker row 정합)
    4. RQ enqueue with job_id arg
    5. RunNowResponse 반환 (job_id 동일)
    """
    enqueue_lock_key = RedisKey.rate_limit("collection_enqueue", str(user_id))
    acquired = await redis.set(
        enqueue_lock_key, "1", nx=True, ex=_ENQUEUE_LOCK_TTL_SECONDS
    )
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": ErrorCode.COLLECTION_ALREADY_RUNNING.value,
                "message": "최근 트리거가 처리 중입니다. 잠시 후 다시 시도해주세요.",
            },
        )
    if await redis.exists(RedisKey.collection_lock(user_id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": ErrorCode.COLLECTION_ALREADY_RUNNING.value,
                "message": "이미 수집 잡이 진행 중입니다.",
            },
        )

    sentinel_id = await _get_sentinel_source_id(db)
    job = CollectionJob(
        job_id=uuid4(),
        user_id=user_id,
        source_id=sentinel_id,
        job_type=JobType.DAILY_COLLECT.value,
        status=CollectionJobStatus.QUEUED.value,
    )
    db.add(job)
    await db.commit()

    # (round 3 R2-S01) enqueue 실패 시 queued row 가 stale 로 남는 것 방지 —
    # try/except 로 감싸 실패 시 status=FAILED + failure_reason 마킹.
    try:
        _enqueue_collection_job(user_id=user_id, job_id=job.job_id)
    except Exception as exc:
        job.status = CollectionJobStatus.FAILED.value
        job.failure_reason = f"enqueue_failed: {type(exc).__name__}: {exc}"[:2000]
        job.finished_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": "수집 잡 큐 등록 실패. 잠시 후 다시 시도해주세요.",
            },
        ) from exc

    today = date.today()
    eta = deterministic_jitter_seconds(user_id, today) + 5
    return RunNowResponse(job_id=job.job_id, eta_seconds=eta)


async def _get_sentinel_source_id(db: AsyncSession) -> UUID:
    stmt = select(Source.source_id).where(Source.name == LLM_SEARCH_SENTINEL_NAME)
    source_id = (await db.execute(stmt)).scalar_one_or_none()
    if source_id is None:
        raise RuntimeError(
            f"sentinel source '{LLM_SEARCH_SENTINEL_NAME}' 미시드 — alembic 0003 적용 필요"
        )
    return source_id


def _enqueue_collection_job(*, user_id: UUID, job_id: UUID) -> None:
    """RQ enqueue. worker function 본문은 backend/app/worker/jobs/collection.py.

    (S-01 fix) job_id 인자를 worker 에 전달 → 응답 job_id 와 동일 row 갱신.
    """
    import redis as sync_redis
    from rq import Queue, Retry

    settings = get_settings()
    sync_conn = sync_redis.Redis.from_url(settings.REDIS_URL_QUEUE)
    queue = Queue("default", connection=sync_conn)
    queue.enqueue(
        "app.worker.jobs.collection.collection_job",
        args=(str(user_id), str(job_id)),
        job_timeout=7200,
        failure_ttl=86_400,
        result_ttl=3_600,
        retry=Retry(max=3, interval=[60, 300, 900]),
    )


__all__ = ["get_my_jobs", "trigger_run_now"]
