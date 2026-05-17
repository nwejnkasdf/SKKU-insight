"""HybridDLifecycleEvaluator — LifecycleEvaluator Protocol 의 D 하이브리드 구현.

A7 결정 #13 하이브리드 + #14 collection hook + #19 Strict 검증.

D 하이브리드:
- 신규 식별 (identify_emerging): LLM 호출 (high slot) + Strict 검증 (4 룰).
- 전이 (evaluate_transitions): no LLM, 룰 기반 (rule_evaluator.py).
- 병합 (evaluate_merges): 주간 LLM 호출 (leaf_merge_evaluator.py).

대체 평가자 (env LIFECYCLE_EVALUATOR=batch_llm) 는 1차 시연 미구현.
"""
from __future__ import annotations

import logging
from uuid import UUID

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import LeafTopicStatus, TraversalStatus
from app.db.models import DynamicLeafTopic, UserCSOTraversal
from app.leaf_lifecycle.leaf_merge_evaluator import evaluate_merges_for_user
from app.leaf_lifecycle.llm_identifier import (
    identify_emerging_with_validation,
)
from app.leaf_lifecycle.protocol import (
    LifecycleSignals,
    MergeProposal,
    NewLeafCandidate,
    StateTransition,
)
from app.leaf_lifecycle.rule_evaluator import evaluate_rule_transitions
from app.llm_provider.protocol import LLMProvider

logger = logging.getLogger(__name__)


class HybridDLifecycleEvaluator:
    """D 하이브리드 — LLM 식별·병합 + 룰 전이. 1차 시연 default."""

    def __init__(
        self,
        db: AsyncSession,
        provider: LLMProvider,
        graph: nx.DiGraph,
    ) -> None:
        self.db = db
        self.provider = provider
        self.graph = graph

    async def identify_emerging(
        self,
        user_id: UUID,
        new_documents: list[UUID],
        existing_leaves: list[UUID],
    ) -> list[NewLeafCandidate]:
        """LLM identify_emerging + Strict 검증 + anchor retry.

        existing_leaves ID list 를 caller 가 전달 — 본 함수가 ORM lookup.
        """
        # 1. ORM lookup — caller 가 ID 만 전달했으므로.
        leaf_rows = list(
            (
                await self.db.execute(
                    select(DynamicLeafTopic).where(
                        DynamicLeafTopic.leaf_topic_id.in_(existing_leaves),
                        DynamicLeafTopic.status == LeafTopicStatus.ACTIVE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        trace_rows = list(
            (
                await self.db.execute(
                    select(UserCSOTraversal).where(
                        UserCSOTraversal.user_id == user_id,
                        UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        # 2. LLM 호출 + 검증 + retry.
        results = await identify_emerging_with_validation(
            self.db,
            self.provider,
            self.graph,
            user_id,
            new_documents,
            leaf_rows,
            trace_rows,
        )
        return [r.candidate for r in results if r.accepted]

    async def evaluate_transitions(
        self,
        user_id: UUID,
        leaves: list[UUID],
        signals: LifecycleSignals,
    ) -> list[StateTransition]:
        """룰 기반 전이 — rule_evaluator.evaluate_rule_transitions 로 위임."""
        leaf_rows = list(
            (
                await self.db.execute(
                    select(DynamicLeafTopic).where(
                        DynamicLeafTopic.leaf_topic_id.in_(leaves)
                    )
                )
            )
            .scalars()
            .all()
        )
        return evaluate_rule_transitions(leaf_rows, signals)

    async def evaluate_merges(
        self,
        user_id: UUID,
        leaves: list[UUID],
    ) -> list[MergeProposal]:
        """주간 LLM 호출 — leaf_merge_evaluator.evaluate_merges_for_user 로 위임.

        `leaves` 인자는 protocol 시그니처 정합용. 실제는 본 함수가 ACTIVE leaf 직접 lookup.
        """
        _ = leaves  # protocol 시그니처
        return await evaluate_merges_for_user(self.db, self.provider, user_id)


__all__ = ["HybridDLifecycleEvaluator"]
