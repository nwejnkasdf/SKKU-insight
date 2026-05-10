"""interest_decay worker stub — A6 (interest-bayesian) 가 본문 구현.

cron = `INTEREST_DECAY_CRON` (default `0 0 * * *` UTC = 매일 자정).
A6 책임: active day 차이 없으면 no-op. 차이 있으면 UserInterestState.long_alpha/beta /
short_alpha/beta 시간 감쇠 (반감기 7/60 active days).
"""
from __future__ import annotations


def interest_decay_job() -> None:
    """A6 에서 구현."""
    raise NotImplementedError(
        "interest_decay_job 본문은 A6 (interest-bayesian) 에이전트 책임."
    )


__all__ = ["interest_decay_job"]
