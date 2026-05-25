"""collection worker — v13 라운드 A4 본문.

RQ sync entry. cron 호출 시 user_id_str=None → 전체 active user 순회.
manual `POST /collection/jobs/me/run-now` 호출 시 user_id_str=<UUID 문자열> → 단일 user.

각 user 별:
1. deterministic jitter (`hash(user_id, today) % 300s`) → asyncio.sleep
2. orchestrator.run_collection_for_user 호출
3. CollectionAlreadyRunning 은 정상 skip 로 처리 (다른 worker 가 진행 중)

엔진/redis/provider 는 본 worker 프로세스가 직접 부트 (FastAPI lifespan 미사용 컨텍스트).

RQ retry: queue.enqueue(retry=Retry(max=3, interval=[60,300,900])) — service.py 에서 설정.
본 함수가 raise 시 RQ 가 재시도. orchestrator 가 정상 종료한 FAILED 는 retry 안 됨.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.collection.orchestrator import (
    CollectionAlreadyRunning,
    deterministic_jitter_seconds,
    run_collection_for_user,
)
from app.config import get_settings
from app.contracts import CollectionJobStatus, LLMProviderType
from app.db.models import CollectionJob, User
from app.llm_provider import get_provider
from app.llm_provider.protocol import LLMBudgetExceeded, ProviderError
from app.redis import get_redis

logger = logging.getLogger(__name__)

_USER_LIMIT = 1000  # 1차 시연 보호 cap (active user 일괄 순회)


def collection_job(
    user_id_str: str | None = None,
    job_id_str: str | None = None,
) -> None:
    """RQ sync entry.

    (v13 round 2 S-01) job_id_str 추가:
    - user_id_str=None → cron 전체 (모든 active user 순회)
    - user_id_str=<UUID>, job_id_str=None → manual 단일 user, 신규 job 생성
    - user_id_str=<UUID>, job_id_str=<UUID> → run-now (service 가 queued row 먼저 INSERT,
      그 job_id 그대로 받아 RUNNING 전이) — 응답 job_id 와 row 정합 보장
    """
    asyncio.run(_async_collection_job(user_id_str, job_id_str))


async def _async_collection_job(
    user_id_str: str | None, job_id_str: str | None
) -> None:
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=settings.PG_WORKER_POOL_MIN,
        max_overflow=settings.PG_WORKER_POOL_MAX,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = get_redis("default")
    provider = get_provider(settings.LLM_PROVIDER or LLMProviderType.MOCK)

    try:
        if user_id_str:
            user_ids: list[UUID] = [UUID(user_id_str)]
        else:
            async with session_factory() as session:
                user_ids = await _select_active_users(session)
        if not user_ids:
            logger.info("collection_job: no active users")
            return

        sem = asyncio.Semaphore(settings.COLLECTION_PER_USER_PARALLEL)
        today = date.today()
        existing_job_id = UUID(job_id_str) if job_id_str else None

        is_run_now = user_id_str is not None

        async def _run_one(uid: UUID) -> None:
            async with sem:
                # Manual run-now should feel immediate in the admin console.
                # Cron fan-out keeps deterministic jitter to avoid a thundering herd.
                sleep_for = 0 if is_run_now else deterministic_jitter_seconds(uid, today)
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                async with session_factory() as session:
                    # (C-63, 2026-05-26) Daily trace mutation step — collection 직전.
                    # 옛 C-61 click hook (실시간 trace creation) 폐기 → 누적 event 분석.
                    # admin "Day simulation" 도 본 경로 통과 (시연 cron 정확히 일치).
                    try:
                        from app.traversal.daily_trace_update import (
                            update_traces_from_recent_events,
                        )

                        updated = await update_traces_from_recent_events(
                            session, uid
                        )
                        await session.commit()
                        if updated > 0:
                            logger.info(
                                "trace_update user=%s updated_traces=%d", uid, updated
                            )
                    except Exception as exc:
                        await session.rollback()
                        logger.warning(
                            "daily trace_update failed user=%s err=%s — proceed with collection",
                            uid,
                            exc,
                        )
                    try:
                        # 단일 user 호출 (run-now) 만 existing_job_id 전달.
                        # cron 다중 user 호출은 신규 row 생성 (job_id_str=None).
                        result = await run_collection_for_user(
                            session,
                            redis,
                            provider,
                            uid,
                            existing_job_id=(
                                existing_job_id if user_id_str else None
                            ),
                        )
                        logger.info(
                            "collection_job done user=%s status=%s leaves=%d docs=%d",
                            uid,
                            result.status.value,
                            result.leaves_processed,
                            result.documents_inserted,
                        )
                    except CollectionAlreadyRunning:
                        if existing_job_id is not None:
                            await _mark_existing_job_skipped(
                                session, existing_job_id
                            )
                        logger.info(
                            "collection_job skipped (already running) user=%s", uid
                        )
                    except (ProviderError, LLMBudgetExceeded) as exc:
                        # (S-03) run-now 모드는 RQ retry 위해 propagate. cron 모드는
                        # 본 user 만 실패 처리 후 다른 user 영향 차단.
                        logger.warning(
                            "collection_job leaf provider failure user=%s: %s",
                            uid,
                            exc,
                        )
                        if is_run_now:
                            raise

        await asyncio.gather(*(_run_one(uid) for uid in user_ids))
    finally:
        await engine.dispose()


async def _select_active_users(session: AsyncSession) -> list[UUID]:
    """onboarding 완료 + 삭제 안 됨. 1000건 cap."""
    stmt = (
        select(User.user_id)
        .where(
            User.onboarding_complete.is_(True),
            User.deleted_at.is_(None),
        )
        .limit(_USER_LIMIT)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def _mark_existing_job_skipped(
    session: AsyncSession, job_id: UUID
) -> None:
    stmt = select(CollectionJob).where(CollectionJob.job_id == job_id)
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return
    job.status = CollectionJobStatus.SKIPPED.value
    job.finished_at = datetime.now(UTC)
    job.failure_reason = "collection_already_running"
    await session.commit()


__all__ = ["collection_job"]
