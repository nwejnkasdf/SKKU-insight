"""LifecycleEvaluator Protocol + dataclass.

module-boundaries.md §LifecycleEvaluator. D 하이브리드 vs B 배치 평가를 갈아끼우기 위한
추상화. 1차 시연 default = HybridDLifecycleEvaluator (env LIFECYCLE_EVALUATOR=hybrid_d).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass(slots=True, frozen=True)
class NewLeafCandidate:
    """identify_emerging LLM 응답의 1 후보. Strict 검증 (결정 #19) 통과 후 사용.

    leaf-topic-lifecycle.md L65~102 prompt 응답 schema.
    """

    label_ko: str
    label_en: str
    cso_topic_ids: list[UUID]            # trace_anchor_required 검증 대상
    supporting_document_ids: list[UUID]   # min 3 (LEAF_EMERGING_SUPPORTING_DOCUMENTS_MIN)
    confidence: float                     # min 0.6 (LEAF_EMERGING_CONFIDENCE_MIN)
    rationale: str


@dataclass(slots=True, frozen=True)
class StateTransition:
    """룰 기반 leaf 전이 1건. evaluate_transitions 반환."""

    leaf_topic_id: UUID
    from_status: str   # emerging / active / stale
    to_status: str     # active / stale / archived
    reason: str        # "window_promotion" / "idle_demotion" / "reactivation"


@dataclass(slots=True, frozen=True)
class MergeProposal:
    """주간 evaluate_merges LLM 응답. 1 merge group.

    leaf-topic-lifecycle.md L104~131 prompt 응답.
    """

    primary_leaf_id: UUID
    merged_leaf_ids: list[UUID]   # 1+ leaf — primary 로 통합
    label_after_merge_ko: str
    label_after_merge_en: str
    rationale: str


@dataclass(slots=True)
class LifecycleSignals:
    """evaluate_transitions 입력 — 사용자별 leaf 의 시간 window 카운터.

    daily cron 이 사용자별로 본 dataclass 를 채워 evaluate_transitions 호출.
    rule_evaluator.py 가 사용. 각 dict 의 key = leaf_topic_id.
    """

    # 최근 N active days 안 매핑 Document 수.
    documents_in_window_7d: dict[UUID, int] = field(default_factory=dict)
    # 최근 N active days 안 click/save/dwell_tick 카운트.
    interest_signals_in_window_7d: dict[UUID, int] = field(default_factory=dict)
    # leaf 의 last_signal_active_day 와 user.active_day_counter 차이.
    idle_active_days: dict[UUID, int] = field(default_factory=dict)


class LifecycleEvaluator(Protocol):
    """D 하이브리드 vs B 배치 평가 추상화.

    LIFECYCLE_EVALUATOR env 로 토글. 1차 시연 = HybridDLifecycleEvaluator.
    """

    async def identify_emerging(
        self,
        user_id: UUID,
        new_documents: list[UUID],     # 최근 24h 사용자 own Document + 인터랙션 (input D)
        existing_leaves: list[UUID],   # 기존 active leaf ids (dedup 비교)
    ) -> list[NewLeafCandidate]:
        """새 emerging 후보 (LLM 호출 + Strict 검증).

        D 하이브리드: LLM `identify_emerging` 호출 후 룰 검증 (confidence, supporting,
        anchor, dedup) 통과 candidate 만 반환. trace_anchor 위반 시 retry cap=1.
        B 배치 (대체 평가자): 룰 기반 keyword extraction 또는 클러스터링 (미구현).

        new_documents/existing_leaves 는 caller 가 미리 lookup 해서 전달 (Document
        ORM 전체가 아닌 ID list — LLM 호출자가 필요 시 추가 lookup).
        """
        ...

    async def evaluate_transitions(
        self,
        user_id: UUID,
        leaves: list[UUID],
        signals: LifecycleSignals,
    ) -> list[StateTransition]:
        """룰 기반 전이 (emerging→active, active→stale, stale→active, stale→archived 등).

        no LLM. rule_evaluator.py 에 위임. 일부 전이는 daily cron, 일부는 ingest 직후
        즉시 평가 (결정 #13 하이브리드).
        """
        ...

    async def evaluate_merges(
        self,
        user_id: UUID,
        leaves: list[UUID],
    ) -> list[MergeProposal]:
        """주간 LLM `evaluate_merges` 호출. label/Jaccard 임계 통과 group 만 반환."""
        ...


__all__ = [
    "LifecycleEvaluator",
    "LifecycleSignals",
    "MergeProposal",
    "NewLeafCandidate",
    "StateTransition",
]
