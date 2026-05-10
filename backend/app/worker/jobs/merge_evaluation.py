"""merge_evaluation worker stub — A7 (leaf-lifecycle + traversal) 가 본문 구현.

cron = `MERGE_EVALUATION_CRON` (default `0 3 * * 1` UTC = 매주 월 03:00).
A7 책임: 동적 리프 토픽 간 병합 후보 평가 (LLM 1회/주/사용자). LifecycleEvaluator hybrid_d.
"""
from __future__ import annotations


def merge_evaluation_job() -> None:
    """A7 에서 구현."""
    raise NotImplementedError(
        "merge_evaluation_job 본문은 A7 (leaf-lifecycle) 에이전트 책임."
    )


__all__ = ["merge_evaluation_job"]
