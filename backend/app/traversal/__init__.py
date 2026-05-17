"""app/traversal — A7 leaf-lifecycle + traversal 의 trace 운영 모듈.

UserCSOTraversal trace 의 5 operation (extend / retract / split / archive / merge)
+ 3단계 강등 (active→stale→retract→archived) + leaf 재배치 LLM 호출.

핵심 entry point: `DefaultTraversalEngine` (protocol.py 의 `TraversalEngine` Protocol 구현).

A6 협업: propagation.py 가 `get_active_traces(user_id)` read 호출.
A8 의존: queries 모듈의 `get_current_topics / get_adjacent_topics / get_descendant_leaves /
get_emerging_leaves` (current/adjacent/discovery 슬롯 후보).
"""
from __future__ import annotations

from app.traversal.default import DefaultTraversalEngine
from app.traversal.protocol import (
    MergePlan,
    NoOp,
    RetractPlan,
    SplitPlan,
    TraversalDelta,
    TraversalEngine,
)

__all__ = [
    "DefaultTraversalEngine",
    "MergePlan",
    "NoOp",
    "RetractPlan",
    "SplitPlan",
    "TraversalDelta",
    "TraversalEngine",
]
