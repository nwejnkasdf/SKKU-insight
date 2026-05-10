"""consent 비즈니스 로직 — GET·POST·revoke·account-deletion.

account-deletion 은 RQ async + worker (decision-backlog C-2 부분 해소, A2 결정 2026-05-11):
  - endpoint 는 enqueue + Redis lock + 즉시 토큰/캐시 정리만
  - worker 함수가 sync session 으로 CASCADE DELETE + Redis namespace 정리
  - expected_deletion_by = now() + 300s
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status
from rq import Queue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import ErrorCode, ErrorResponse, RedisKey
from app.db.models import User, UserConsent
from app.security.consent_cache import invalidate_consent_cache
from app.security.jwt import revoke_all_user_refresh

from .schemas import (
    AccountDeletionRequest,
    AccountDeletionResponse,
    ConsentRecord,
    ConsentRequest,
    ConsentRevokeRequest,
    ConsentStateResponse,
)

ACCOUNT_DELETION_LOCK_TTL = 600
ACCOUNT_DELETION_BUFFER_SECONDS = 300


def _http_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    *,
    request: Request,
    details: dict | None = None,
) -> HTTPException:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        code=code, message=message, details=details, request_id=request_id
    ).model_dump(mode="json")
    return HTTPException(status_code=status_code, detail=body)


async def _load_records(user_id: UUID, db: AsyncSession) -> list[UserConsent]:
    stmt = select(UserConsent).where(UserConsent.user_id == user_id).order_by(
        UserConsent.agreed_at.desc()
    )
    return list((await db.execute(stmt)).scalars().all())


def _build_state(user: User, records: list[UserConsent]) -> ConsentStateResponse:
    active = any(
        r.consent_type == "personalization" and r.revoked_at is None for r in records
    )
    return ConsentStateResponse(
        user_id=user.user_id,
        records=[
            ConsentRecord(
                consent_id=r.consent_id,
                consent_type="personalization",
                agreed_at=r.agreed_at,
                revoked_at=r.revoked_at,
            )
            for r in records
        ],
        active=active,
        onboarding_required=not user.onboarding_complete,
    )


async def get_state(user: User, db: AsyncSession) -> ConsentStateResponse:
    return _build_state(user, await _load_records(user.user_id, db))


async def register(
    user: User,
    payload: ConsentRequest,
    *,
    request: Request,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> ConsentStateResponse:
    """동의 등록. agreed=true 만 허용 (false 는 422). 이미 active 면 idempotent no-op."""
    if not payload.agreed:
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCode.VALIDATION_ERROR,
            "agreed=false 는 허용되지 않습니다. 철회는 /consent/revoke 를 사용하세요.",
            request=request,
        )
    records = await _load_records(user.user_id, db)
    already_active = any(
        r.consent_type == "personalization" and r.revoked_at is None for r in records
    )
    if not already_active:
        db.add(
            UserConsent(
                user_id=user.user_id,
                consent_type="personalization",
                agreed_at=datetime.now(UTC),
            )
        )
        await db.commit()
        await invalidate_consent_cache(user.user_id, redis)
        records = await _load_records(user.user_id, db)
    return _build_state(user, records)


async def revoke(
    user: User,
    payload: ConsentRevokeRequest,
    *,
    request: Request,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> ConsentStateResponse:
    """철회. revoked_at 마킹 + 추천 캐시 폐기 + consent cache invalidate."""
    records = await _load_records(user.user_id, db)
    active = [
        r for r in records if r.consent_type == "personalization" and r.revoked_at is None
    ]
    if not active:
        # 이미 비활성 — idempotent no-op (또는 409 도 가능, 1차는 conservative pass-through)
        return _build_state(user, records)
    now = datetime.now(UTC)
    for r in active:
        r.revoked_at = now
    await db.commit()
    await invalidate_consent_cache(user.user_id, redis)
    await redis.delete(RedisKey.recommendation_cache(user.user_id))
    return _build_state(user, await _load_records(user.user_id, db))


async def request_account_deletion(
    user: User,
    payload: AccountDeletionRequest,
    *,
    request: Request,
    db: AsyncSession,
    redis: aioredis.Redis,
    queue_redis: aioredis.Redis,
) -> AccountDeletionResponse:
    """RQ async 큐잉 + 즉시 토큰·캐시 정리 + expected_deletion_by 반환 (C-2 부분 해소).

    중복 enqueue 방지: `account_deletion:{user_id}` NX EX=600 lock.
    worker function 본문은 backend/app/worker/jobs/account_deletion.py.
    """
    _ = payload  # reason 은 worker 로 그대로 전달 — 현재는 사용 안 함
    # codex v2 #2: JwtAuthMiddleware 가 본 lock 존재 시 access 차단 (deletion gate).
    lock_key = RedisKey.account_deletion_pending(user.user_id)
    locked = await redis.set(lock_key, "1", nx=True, ex=ACCOUNT_DELETION_LOCK_TTL)
    if not locked:
        raise _http_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.CONSENT_DELETION_IN_PROGRESS,
            "이미 계정 삭제 요청이 진행 중입니다.",
            request=request,
        )

    # 즉시 보안 정리 (worker 실행 전에라도 모든 세션 무효화)
    await revoke_all_user_refresh(user.user_id, redis)
    await invalidate_consent_cache(user.user_id, redis)
    await redis.delete(RedisKey.recommendation_cache(user.user_id))

    # RQ enqueue (worker function 본문은 A2 가 backend/app/worker/jobs/account_deletion.py 에 구현)
    queue = _enqueue_account_deletion(
        user_id=user.user_id,
        reason=payload.reason,
        queue_redis=queue_redis,
    )
    _ = queue  # rq.Queue 반환은 현재 사용 안 함

    request_id = uuid4()
    expected_by = datetime.now(UTC) + timedelta(seconds=ACCOUNT_DELETION_BUFFER_SECONDS)
    return AccountDeletionResponse(
        request_id=request_id,
        status="queued",
        expected_deletion_by=expected_by,
    )


def _enqueue_account_deletion(
    *,
    user_id: UUID,
    reason: str | None,
    queue_redis: aioredis.Redis,
) -> Queue:
    """RQ enqueue. queue_redis 는 sync redis 가 아니라 async client 의 connection 정보를
    사용해 RQ 가 자체적으로 sync connection 을 다시 생성 (RQ 표준).
    """
    # RQ 는 sync Redis 클라이언트를 요구하므로 URL 로부터 sync 연결을 만든다.
    import redis as sync_redis

    settings = get_settings()
    sync_conn = sync_redis.Redis.from_url(settings.REDIS_URL_QUEUE)
    queue = Queue("default", connection=sync_conn)
    queue.enqueue(
        "app.worker.jobs.account_deletion.delete_user_account",
        args=(str(user_id), reason),
        job_timeout=300,
        retry=None,
        failure_ttl=86_400,
        result_ttl=86_400,
    )
    _ = queue_redis  # unused — async 가 아닌 sync RQ 패턴
    return queue


__all__ = [
    "get_state",
    "register",
    "request_account_deletion",
    "revoke",
]
