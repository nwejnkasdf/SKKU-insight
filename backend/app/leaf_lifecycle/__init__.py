"""app/leaf_lifecycle — A7 DynamicLeafTopic 라이프사이클 평가자.

상태 머신 (decisions.md §4 + leaf-topic-lifecycle.md):
  emerging → active   (7 active days + 5 docs + 2 interest signals)
  emerging → archived (14 active days idle)
  active   → stale    (21 active days idle)
  stale    → active   (7 days + 3 docs + 1 interest, 재활성화)
  stale    → archived (90 active days idle)
  → merged            (LLM 주 1회 evaluate_merges)

A7 결정 매트릭스 (decisions.md §12):
- #13 룰 기반 전이는 하이브리드 — 활성 신호 즉시, 강등 daily cron
- #14 emerging 식별 trigger = collection daily cron 직후 hook
- #15 trace_anchor 위반 = 자동 거부 + 즉시 재호출 (retry cap=1)
- #16 merged leaf = 모든 추천 제외
- #18 emerging input = A4 collection union UserEvent click/save (옵션 D)
- #19 Strict 검증 — confidence ≥ 0.6 + supporting_docs ≥ 3 + label_similarity dedup
"""
from __future__ import annotations

from app.leaf_lifecycle.hybrid_d import HybridDLifecycleEvaluator
from app.leaf_lifecycle.protocol import (
    LifecycleEvaluator,
    LifecycleSignals,
    MergeProposal,
    NewLeafCandidate,
    StateTransition,
)

__all__ = [
    "HybridDLifecycleEvaluator",
    "LifecycleEvaluator",
    "LifecycleSignals",
    "MergeProposal",
    "NewLeafCandidate",
    "StateTransition",
]
