"""C-63 (2026-05-26) Daily trace mutation step — collection orchestrator 직전 호출.

옛 C-61 click hook (interest/service.py 의 실시간 trace creation) 폐기. 대신:
1. user_event 가 평소 ingest 되어 누적
2. Daily collection (또는 admin "Day simulation" 버튼) 의 LLM 검색 시작 직전,
   본 함수가 누적 event 분석 → 임계 통과 cso 마다 trace 변동 수행
3. 이후 정상 collection 진행 (LLM 검색은 갱신된 trace 영역 기반)

Effect: SAVE/CLICK 1번 만으로 dashboard cascading 변동 차단. trace mutation 시점이
daily 1회로 묶임 → consumer (dashboard) 가 producer (daily cron) 산출물 안정 사용.

Spec (C-63 결정):
- event_type ∈ {click, save, dwell_tick}, document_id NOT NULL
- 최근 N active days (default 14, onboarding boost 만료 정합)
- doc → DocumentTopic.cso_topic_id 매핑 (1 doc → N cso)
- cso 별 event 누적 count ≥ threshold (default 2) → trace 형성/promote
- 첫 behavioral trace 신호 시 boost trace cleanup (C-62 정합)
"""
from __future__ import annotations

import logging
from collections import Counter
from uuid import UUID

import networkx as nx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentTopic, User, UserEvent

logger = logging.getLogger(__name__)

# C-61 hook 의 _TRACE_CREATION_EVENT_TYPES 와 동일 집합. interest/service.py 가
# 폐기되면서 본 파일이 단일 SOR.
_TRACE_TRIGGER_EVENT_TYPES = frozenset({"click", "save", "dwell_tick"})


async def _collect_cso_event_counts(
    db: AsyncSession, user_id: UUID, lookback_active_days: int
) -> Counter[UUID]:
    """user_event ⨯ DocumentTopic JOIN 으로 cso 별 누적 count.

    - 같은 doc 의 multi event (click + save) 둘 다 counted (별개 event_id).
    - 같은 event 의 doc 이 multi cso 매핑이면 각 cso 마다 +1 (의도 — broad 활동
      신호일수록 trace 형성 가속).
    """
    user_active_day = (
        await db.execute(
            select(User.active_day_counter).where(User.user_id == user_id)
        )
    ).scalar_one_or_none()
    if user_active_day is None:
        return Counter()
    cutoff = max(0, int(user_active_day or 0) - lookback_active_days)
    stmt = (
        select(DocumentTopic.cso_topic_id, func.count())
        .select_from(UserEvent)
        .join(DocumentTopic, DocumentTopic.document_id == UserEvent.document_id)
        .where(
            UserEvent.user_id == user_id,
            UserEvent.event_type.in_(_TRACE_TRIGGER_EVENT_TYPES),
            UserEvent.document_id.is_not(None),
            UserEvent.active_day_at_event >= cutoff,
            DocumentTopic.cso_topic_id.is_not(None),
        )
        .group_by(DocumentTopic.cso_topic_id)
    )
    rows = (await db.execute(stmt)).all()
    return Counter({row[0]: int(row[1]) for row in rows})


async def update_traces_from_recent_events(
    db: AsyncSession,
    user_id: UUID,
    *,
    threshold: int = 2,
    lookback_active_days: int = 14,
) -> int:
    """daily collection 직전 호출. 누적 event 임계 통과 cso 마다 trace 변동.

    Args:
        threshold: cso 별 event count 임계 (default 2). 사용자 결정 (C-63).
        lookback_active_days: 누적 기간 (default 14 active days).

    Returns: 변경된 trace 수 (new_trace + promoted). boost cleanup 은 별개.
    """
    cso_counter = await _collect_cso_event_counts(
        db, user_id, lookback_active_days=lookback_active_days
    )
    qualifying_csos = [cso for cso, count in cso_counter.items() if count >= threshold]
    if not qualifying_csos:
        return 0

    user_active_day = (
        await db.execute(
            select(User.active_day_counter).where(User.user_id == user_id)
        )
    ).scalar_one_or_none()
    if user_active_day is None:
        return 0
    current_active_day = int(user_active_day or 0)

    # DefaultTraversalEngine.ingest_event 재사용 — 매칭 / promote / 신규 trace 분기 동일.
    # graph 의존 없음 (extend 등 본 step 에서 안 함). empty DiGraph 안전 instantiate.
    from app.traversal.default import DefaultTraversalEngine

    engine = DefaultTraversalEngine(db, None, nx.DiGraph())  # type: ignore[arg-type]

    updated = 0
    any_behavioral_signal = False
    for cso_id in qualifying_csos:
        try:
            delta = await engine.ingest_event(
                user_id, current_active_day, [cso_id]
            )
        except RuntimeError as exc:
            # active_cap_exceeded 등 — 사용자별 cap 도달 시 무시.
            logger.warning(
                "daily trace_update: ingest_event failed user=%s cso=%s err=%s",
                user_id,
                cso_id,
                exc,
            )
            continue
        if delta in ("new_trace", "promoted"):
            updated += 1
            any_behavioral_signal = True

    # 첫 behavioral 신호 시 사용자의 boost trace 일괄 삭제 (C-62 정합).
    if any_behavioral_signal:
        from app.interest.service import _cleanup_boost_traces

        await _cleanup_boost_traces(db, user_id)

    logger.info(
        "daily trace_update user=%s qualifying_csos=%d updated=%d",
        user_id,
        len(qualifying_csos),
        updated,
    )
    return updated


__all__ = ["update_traces_from_recent_events"]
