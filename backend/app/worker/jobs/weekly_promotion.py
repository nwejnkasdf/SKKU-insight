"""C-53 (2026-05-24) weekly promotion job — discovery/adjacent 카드의 강한 신호 → core 부활.

사용자 디자인 의도 (논의):
> "discovery / adjacent 의 목적이 core 확대도 있으니까. 강한 신호 (save) 시 core 로 부활"

흐름 (주 1회, WEEKLY_PROMOTION_CRON = "0 18 * * 0" 일요일 18 UTC):
1. 직전 7-day 안 발생한 UserEvent.event_type='save' 조회
2. 각 save event 의 document_id → 같은 user 의 Recommendation row (origin_type IS NOT NULL)
3. Reincarnation (origin_type='reincarnation', origin_ref=archived_trace_id):
   - 해당 trace.status: 'archived' → 'active'
   - last_activity_active_day = user.active_day_counter (현재)
   - path / score_tail 그대로 보존 (Serendipity "taste reincarnation" 본질)
4. Fusion (origin_type='fusion', origin_ref=bridge_cso_topic_id):
   - 새 active UserCSOTraversal INSERT (path=[bridge_cso], started_active_day=현재)
   - C-40 first trace 생성 hook 와 동일 패턴
5. duplicate (같은 origin 같은 user 의 save 여러 번) = 1번만 promote

active cap 가드 X — 사용자 무제한 결정 (C-53 디자인).

본 job 이 promotion 결정 시 recommendation_cache invalidate — 다음 dashboard 조회 시
core slot 에 새 active trace 의 매핑 자료 자연 표시.

Anti-pattern 회피:
- (#1) cache-before-commit — db.commit() 성공 후 redis DEL
- (#4) lock release race — uuid4 token + Lua atomic CAS release (A7 trace_merge 패턴)
- (#3) per-user 실패 isolation — try/except/finally + commit (다른 사용자 영향 X)
- (C-53 followup) per-user traversal_lock — daily_lifecycle / trace_merge / interest hook 와
  같은 user-mutex 공유. trace mutation race 차단 (status archived→active / 신규 INSERT).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import RedisKey, TraversalStatus
from app.db.models import (
    Recommendation,
    User,
    UserCSOTraversal,
    UserEvent,
)
from app.db.session import AsyncSessionLocal
from app.redis import get_redis

logger = logging.getLogger("weekly_promotion_job")

_PROMOTION_WINDOW_DAYS = 7

# (C-53 followup) lock release Lua atomic — 자기 token 일치 시만 DEL (TTL 만료 후 다른 worker
# lock 잘못 해제 차단). A7 trace_merge 패턴 답습.
_RELEASE_LOCK_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
)


async def _promote_reincarnation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    archived_trace_id: uuid.UUID,
    current_active_day: int,
) -> bool:
    """archived trace 의 status archived → active. path / score_tail 보존.

    이미 active 면 skip (사용자가 reincarnation 후 다시 save 한 경우). 다른 status
    (stale, merged) 도 skip — 안전.
    """
    stmt = (
        update(UserCSOTraversal)
        .where(
            UserCSOTraversal.trace_id == archived_trace_id,
            UserCSOTraversal.user_id == user_id,
            UserCSOTraversal.status == TraversalStatus.ARCHIVED.value,
        )
        .values(
            status=TraversalStatus.ACTIVE.value,
            last_activity_active_day=current_active_day,
            archived_at_active_day=None,
        )
    )
    result = await db.execute(stmt)
    return (result.rowcount or 0) > 0


async def _promote_fusion(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    bridge_cso_topic_id: uuid.UUID,
    current_active_day: int,
) -> bool:
    """bridge_cso 를 root 로 새 active trace INSERT. C-40 first trace 생성 패턴.

    이미 같은 path 의 active trace 있으면 skip (idempotent — 같은 bridge 여러 번 save).
    """
    existing = (
        await db.execute(
            select(UserCSOTraversal.trace_id).where(
                UserCSOTraversal.user_id == user_id,
                UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
                UserCSOTraversal.path == [bridge_cso_topic_id],
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    new_trace_id = uuid.uuid4()
    db.add(
        UserCSOTraversal(
            trace_id=new_trace_id,
            user_id=user_id,
            path=[bridge_cso_topic_id],
            status=TraversalStatus.ACTIVE.value,
            started_active_day=current_active_day,
            last_activity_active_day=current_active_day,
            score_tail=0.0,
        )
    )
    # (C-66, 2026-05-26) Fusion 새 trace 의 score_tail sync — bridge_cso 의 long_score.
    # 사용자가 fusion discovery 카드를 save 했다 = bridge 영역에 강한 신호. 그 long_score
    # 가 새 trace 의 score_tail 에 반영되어야 core_softmax 다양성 가중치 의미.
    # _promote_reincarnation 은 archive 시점 freeze 값 보존 (Serendipity narrative 정합).
    from app.traversal.operations import sync_score_tail_for_trace
    await db.flush()
    await sync_score_tail_for_trace(db, new_trace_id)
    return True


async def _run() -> int:
    """모든 사용자 순회 — 주 1회 cron entry. return: promotion 발생 수.

    (C-53 followup) 각 사용자마다 traversal_lock 획득. daily_lifecycle_evaluation /
    trace_merge_job / interest hook 와 같은 lock 키 공유 — trace mutation 직렬화.
    """
    redis = get_redis("default")
    settings = get_settings()
    window_start = datetime.now(UTC) - timedelta(days=_PROMOTION_WINDOW_DAYS)
    promotions_total = 0
    reincarnation_total = 0
    fusion_total = 0
    # weekly_promotion 은 SQL UPDATE/INSERT 만 — LLM 호출 없음. TRACE_MERGE_LOCK_TTL_SECONDS
    # 보다 짧아도 충분하나, trace_merge 와 동일 lock 공유 의미상 같은 TTL 사용.
    lock_ttl = settings.TRACE_MERGE_LOCK_TTL_SECONDS

    async with AsyncSessionLocal() as db:
        users = list((await db.execute(select(User))).scalars().all())
        for user in users:
            lock_key = RedisKey.traversal_lock(user.user_id)
            lock_token = str(uuid.uuid4())
            acquired = await redis.set(
                lock_key, lock_token, nx=True, ex=lock_ttl
            )
            if not acquired:
                logger.info(
                    "weekly_promotion lock busy user=%s — skip", user.user_id
                )
                continue
            try:
                # 1. 직전 7-day save event 조회
                save_events = list(
                    (
                        await db.execute(
                            select(UserEvent.document_id)
                            .where(
                                UserEvent.user_id == user.user_id,
                                UserEvent.event_type == "save",
                                UserEvent.created_at >= window_start,
                            )
                            .distinct()
                        )
                    ).scalars().all()
                )
                if not save_events:
                    continue
                # 2. Recommendation 의 origin metadata JOIN
                origin_rows = list(
                    (
                        await db.execute(
                            select(
                                Recommendation.origin_type,
                                Recommendation.origin_ref,
                            )
                            .where(
                                Recommendation.user_id == user.user_id,
                                Recommendation.document_id.in_(save_events),
                                Recommendation.origin_type.isnot(None),
                                Recommendation.origin_ref.isnot(None),
                            )
                            .distinct()
                        )
                    ).all()
                )
                if not origin_rows:
                    continue
                # 3. dedup — 같은 (type, ref) 1번만 처리
                seen: set[tuple[str, uuid.UUID]] = set()
                user_promoted = 0
                for row in origin_rows:
                    key = (row.origin_type, row.origin_ref)
                    if key in seen:
                        continue
                    seen.add(key)
                    if row.origin_type == "reincarnation":
                        if await _promote_reincarnation(
                            db,
                            user_id=user.user_id,
                            archived_trace_id=row.origin_ref,
                            current_active_day=int(user.active_day_counter),
                        ):
                            user_promoted += 1
                            reincarnation_total += 1
                    elif row.origin_type == "fusion":
                        if await _promote_fusion(
                            db,
                            user_id=user.user_id,
                            bridge_cso_topic_id=row.origin_ref,
                            current_active_day=int(user.active_day_counter),
                        ):
                            user_promoted += 1
                            fusion_total += 1
                if user_promoted == 0:
                    continue
                await db.commit()
                promotions_total += user_promoted
                # cache invalidate — 다음 dashboard 조회 시 core slot 에 새 trace 반영
                try:
                    await redis.delete(
                        RedisKey.recommendation_cache(user.user_id)
                    )
                except Exception:
                    logger.warning(
                        "weekly_promotion cache invalidate failed user=%s "
                        "(DB committed)",
                        user.user_id,
                    )
            except Exception:
                logger.exception(
                    "weekly_promotion user=%s failed", user.user_id
                )
                await db.rollback()
            finally:
                # (C-53 followup) Lua atomic CAS — 자기 token 일치 시만 DEL.
                try:
                    await redis.eval(  # type: ignore[misc]
                        _RELEASE_LOCK_LUA, 1, lock_key, lock_token
                    )
                except Exception:
                    logger.warning(
                        "weekly_promotion lock release race user=%s",
                        user.user_id,
                    )
    logger.info(
        "weekly_promotion_job total=%d reincarnation=%d fusion=%d",
        promotions_total,
        reincarnation_total,
        fusion_total,
    )
    return promotions_total


def weekly_promotion_job() -> None:
    """RQ sync entrypoint."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


__all__ = ["weekly_promotion_job"]
