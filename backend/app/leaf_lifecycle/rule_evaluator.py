"""Leaf 룰 기반 전이 — leaf-topic-lifecycle.md L31-159 의사 코드 본문.

A7 결정 #13: 하이브리드 평가 시점.
- 활성 신호 (emerging→active 승격, stale→active 재활성화): ingest 직후 즉시 (no LLM, no cron).
- 강등 (active→stale, stale→archived, emerging→archived): 18 UTC daily cron 일괄.

전이 6종:
  emerging → active   (window 7d + docs 5 + interest 2)
  emerging → archived (idle 14d)
  active   → stale    (idle 21d)
  stale    → active   (window 7d + docs 3 + interest 1)
  stale    → archived (idle 90d)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import LeafTopicStatus
from app.db.models import DynamicLeafTopic
from app.leaf_lifecycle.protocol import LifecycleSignals, StateTransition

logger = logging.getLogger(__name__)


def evaluate_rule_transitions(
    leaves: list[DynamicLeafTopic],
    signals: LifecycleSignals,
) -> list[StateTransition]:
    """순수 함수 — leaf 룰 평가, no SQL.

    caller (HybridDLifecycleEvaluator) 가 SQL execute. 본 함수는 입력에서 결정만.
    """
    settings = get_settings()
    transitions: list[StateTransition] = []
    for leaf in leaves:
        leaf_id = leaf.leaf_topic_id
        idle = signals.idle_active_days.get(leaf_id, 0)
        docs_7d = signals.documents_in_window_7d.get(leaf_id, 0)
        signals_7d = signals.interest_signals_in_window_7d.get(leaf_id, 0)

        if leaf.status == LeafTopicStatus.EMERGING.value:
            # 승격 (활성 신호 — ingest 직후 즉시).
            if (
                docs_7d >= settings.LEAF_ACTIVE_MIN_DOCUMENTS
                and signals_7d >= settings.LEAF_ACTIVE_MIN_INTEREST_SIGNALS
            ):
                transitions.append(
                    StateTransition(
                        leaf_topic_id=leaf_id,
                        from_status=leaf.status,
                        to_status=LeafTopicStatus.ACTIVE.value,
                        reason="window_promotion",
                    )
                )
                continue
            # 강등 (idle 만료 — daily cron).
            if idle >= settings.LEAF_EMERGING_ARCHIVED_IDLE_DAYS:
                transitions.append(
                    StateTransition(
                        leaf_topic_id=leaf_id,
                        from_status=leaf.status,
                        to_status=LeafTopicStatus.ARCHIVED.value,
                        reason="emerging_idle_archived",
                    )
                )
        elif leaf.status == LeafTopicStatus.ACTIVE.value:
            # 강등 active → stale (idle 21d — daily cron).
            if idle >= settings.LEAF_STALE_IDLE_DAYS:
                transitions.append(
                    StateTransition(
                        leaf_topic_id=leaf_id,
                        from_status=leaf.status,
                        to_status=LeafTopicStatus.STALE.value,
                        reason="idle_demotion",
                    )
                )
        elif leaf.status == LeafTopicStatus.STALE.value:
            # 재활성화 (활성 신호 — ingest 직후 즉시).
            if (
                docs_7d >= settings.LEAF_REACTIVATION_MIN_DOCUMENTS
                and signals_7d >= settings.LEAF_REACTIVATION_MIN_INTEREST_SIGNALS
            ):
                transitions.append(
                    StateTransition(
                        leaf_topic_id=leaf_id,
                        from_status=leaf.status,
                        to_status=LeafTopicStatus.ACTIVE.value,
                        reason="reactivation",
                    )
                )
                continue
            # 강등 stale → archived (idle 90d — daily cron).
            if idle >= settings.LEAF_ARCHIVED_IDLE_DAYS:
                transitions.append(
                    StateTransition(
                        leaf_topic_id=leaf_id,
                        from_status=leaf.status,
                        to_status=LeafTopicStatus.ARCHIVED.value,
                        reason="stale_archived",
                    )
                )
        # merged/archived 는 본 함수에서 평가 안 함.
    return transitions


async def apply_transitions(
    db: AsyncSession,
    transitions: list[StateTransition],
) -> int:
    """전이 list 를 SQL UPDATE 일괄 적용.

    atomic UPDATE per leaf (small N). last_signal_active_day 는 별도 갱신
    (active 신호 들어올 때만 — service.ingest 가 함).
    """
    now = datetime.now(UTC)
    applied = 0
    for trans in transitions:
        stmt = (
            update(DynamicLeafTopic)
            .where(
                DynamicLeafTopic.leaf_topic_id == trans.leaf_topic_id,
                DynamicLeafTopic.status == trans.from_status,
            )
            .values(status=trans.to_status)
            .returning(DynamicLeafTopic.leaf_topic_id)
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none() is not None:
            applied += 1
    _ = now
    return applied


__all__ = ["apply_transitions", "evaluate_rule_transitions"]
