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
    """daily collection 직전 호출. 누적 event 임계 통과 cso 들을 **각각 별도** ingest_event
    호출 — 다양한 cluster click 시 cluster 별 trace 형성.

    Args:
        threshold: cso 별 event count 임계 (default 2). 사용자 결정 (C-63).
        lookback_active_days: 누적 기간 (default 14 active days).

    Returns: 변경된 trace 수 (new_trace + promoted + reactivated 합).

    (C-71, 2026-05-26) **C-67 결정 #2 reverse** — qualifying_csos list 통째 1번 호출 →
    cso 별 N 번 호출. C-67 가 1 doc multi-cso 매핑의 trace 과잉 (1 click 4 trace) 차단
    했으나 동시에 **다양한 cluster click 시 1 cluster trace 만 형성** 결함 도입. 실측:
    SE 8 + AI 6 + HCI 2 click → trace 1개 (AI 만, 가장 빈도 높은 cso 첫 매칭) → collection
    결과 35 doc 모두 AI 영역 → narrative 산만.

    14d 활성 사용자 가정 시 multi-cso 매핑 1 doc 만 click 케이스는 비중 낮음 (사용자가
    다양 doc click 압도적). 다양성 우선. qualifying_csos 모두 처리 → cluster 별 trace.

    (C-72, 2026-05-26) `_cleanup_boost_traces` 호출 제거 — boost trace 14d 자연 만료에
    위임 (interest_decay_job:expire_onboarding_boost_traces). 직전 cleanup 정책이 첫
    behavioral 신호 시 **모든** boost DELETE — 사용자가 1 cluster click 만으로도 다른
    cluster boost 다 사라짐 → cso-topic-traversal.md §1.2 "14 active day prior boost"
    narrative 위반. C-72 fix: cluster 별 boost 가 14 active days 동안 자연 유지, 사용자가
    그 cluster doc click 시 그 boost 만 promote (origin: onboarding_boost → behavioral,
    path 보존), 14d 후 활동 없는 boost 만 자연 expire.
    """
    cso_counter = await _collect_cso_event_counts(
        db, user_id, lookback_active_days=lookback_active_days
    )
    qualifying_csos = sorted(
        [cso for cso, count in cso_counter.items() if count >= threshold],
        key=lambda c: (-cso_counter[c], str(c)),
    )
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

    # (C-71) DefaultTraversalEngine.ingest_event 를 cso 별 호출 — 각 cso 마다 매칭 또는
    # 새 trace 1개. graph 의존 없음 (extend 등 본 step 에서 안 함). empty DiGraph 안전.
    from app.traversal.default import DefaultTraversalEngine

    engine = DefaultTraversalEngine(db, None, nx.DiGraph())  # type: ignore[arg-type]

    updated = 0
    delta_counts: Counter[str] = Counter()
    for cso_id in qualifying_csos:
        try:
            delta = await engine.ingest_event(
                user_id, current_active_day, [cso_id]
            )
        except RuntimeError as exc:
            # active_cap_exceeded 등 — 사용자별 cap 도달 시 본 cso skip + 다음 cso 시도.
            logger.warning(
                "daily trace_update: ingest_event failed user=%s cso=%s err=%s",
                user_id,
                cso_id,
                exc,
            )
            continue
        delta_counts[delta] += 1
        # (C-65) "reactivated" delta — stale trace 가 신호로 active 복귀. updated count
        # 에 포함. promoted / new_trace 도 count.
        if delta in ("new_trace", "promoted", "reactivated"):
            updated += 1

    # (C-72) `_cleanup_boost_traces` 호출 제거 — boost 14d 자연 만료에 위임.
    # promote 자체는 ingest_event 안에서 origin (onboarding_boost → behavioral) 만 변경,
    # 같은 trace_id + path 보존. 다른 cluster boost 는 영향 X (자연 유지).

    logger.info(
        "daily trace_update user=%s qualifying_csos=%d updated=%d "
        "deltas=%s (per-cso)",
        user_id,
        len(qualifying_csos),
        updated,
        dict(delta_counts),
    )
    return updated


__all__ = ["update_traces_from_recent_events"]
