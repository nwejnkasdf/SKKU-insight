"""account_deletion worker — User CASCADE DELETE + Redis namespace 정리.

A2 본문 구현 (decision-backlog C-2 부분 해소). 호출 경로:
1) consent.service.request_account_deletion 이 RQ enqueue
2) worker process 가 본 함수 실행 (sync session — RQ 표준)
3) FK ondelete=CASCADE 에 의해 UserConsent / UserEvent / UserInterestState /
   UserCSOTraversal / SavedDocument / HiddenDocument / NotInterestedTopic /
   DynamicLeafTopic (사용자 소유분) / Recommendation 자동 삭제
4) Redis `refresh:{user_id}:*`, `recommendation:{user_id}`, `consent:active:{user_id}`,
   `lock:*:{user_id}`, `events:buffer:{user_id}` DEL

NFR-21 의 30일 grace 는 post-시연 폴리시 — 본 worker 자체가 향후 grace period
도입 시 재활용 (sleep+grace 컬럼 추가 시 본 함수가 분기).
"""
from __future__ import annotations

import logging
from uuid import UUID

import redis as sync_redis
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.contracts import RedisKey
from app.db.models import User

logger = logging.getLogger(__name__)


def delete_user_account(user_id_str: str, reason: str | None = None) -> None:
    """RQ worker 진입점. 동기 함수 — RQ standard.

    Args:
        user_id_str: User.user_id 의 str(UUID).
        reason: 삭제 사유 (감사 로그용, 현재는 로그만).
    """
    logger.info("account_deletion start user_id=%s reason=%s", user_id_str, reason)
    # 명시 변환 — asyncpg/psycopg dialect 별 자동 변환 의존 회피
    user_id = UUID(user_id_str)
    settings = get_settings()
    # RQ worker 는 sync. async DATABASE_URL → sync URL 변환 (asyncpg → psycopg).
    sync_url = _to_sync_url(settings.DATABASE_URL)
    engine = create_engine(sync_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    try:
        with Session(engine) as session:
            session.execute(delete(User).where(User.user_id == user_id))
            session.commit()
    finally:
        engine.dispose()

    redis_conn = sync_redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        # 명시 키 — RedisKey SOR 사용 (check_redis_keys 검증 통과)
        exact_keys = [
            RedisKey.recommendation_cache(user_id),
            RedisKey.consent_active_cache(user_id),
            RedisKey.onboarding_lock(user_id),
            RedisKey.traversal_lock(user_id),
            RedisKey.collection_lock(user_id),
            RedisKey.recommendation_build_lock(user_id),
            RedisKey.event_buffer(user_id),
            # consent.service lock — RedisKey 외 키 (EXPLICIT_ALLOWED_KEYS, P0 후 contracts.py 정식화 검토)
            f"account_deletion:{user_id}",
        ]
        for key in exact_keys:
            redis_conn.delete(key)

        # SCAN 패턴 — refresh:{user_id}:{jti} 의 모든 jti 매치, idempotency 등.
        # RedisKey 가 prefix 매처를 노출하지 않으므로 raw pattern 사용 (SCAN 와일드카드는
        # SOR 룰의 정확 키 생성 범위 외 — check_redis_keys 가 wildcard 포함 패턴 무시).
        scan_patterns = [
            f"refresh:{user_id}:*",
            "refresh_index:*",  # 인덱스는 단방향 HMAC — TTL 14d 자연 만료에 위임
            f"idemp:onboarding:{user_id}:*",
        ]
        for pattern in scan_patterns:
            for key in redis_conn.scan_iter(match=pattern):
                redis_conn.delete(key)
    finally:
        redis_conn.close()
    logger.info("account_deletion done user_id=%s", user_id_str)


def _to_sync_url(async_url: str) -> str:
    """`postgresql+asyncpg://...` → `postgresql+psycopg://...` 변환 (worker 는 sync)."""
    if async_url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + async_url[len("postgresql+asyncpg://"):]
    return async_url


__all__ = ["delete_user_account"]
