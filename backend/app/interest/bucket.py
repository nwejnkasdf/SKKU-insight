"""score → bucket 매핑. interest-bayesian.md §3.

NFR-04 마스킹 — 일반 사용자 응답에는 bucket 만, score 자체는 노출 X.
"""
from __future__ import annotations

from app.contracts import InterestBucket
from app.interest.config_loader import InterestParams


def bucket_for(
    long_score: float, short_score: float, params: InterestParams
) -> InterestBucket:
    """bucket 룰 (interest-bayesian.md 표 그대로).

    | 조건 | bucket |
    |---|---|
    | long ≥ 0.7 AND short ≥ 0.6 | HIGH |
    | long ≥ 0.5 OR short ≥ 0.5 | MEDIUM |
    | long ≥ 0.3 OR short ≥ 0.3 | LOW |
    | 그 외 | NEUTRAL |
    """
    if (
        long_score >= params.bucket_high_long
        and short_score >= params.bucket_high_short
    ):
        return InterestBucket.HIGH
    if (
        long_score >= params.bucket_medium
        or short_score >= params.bucket_medium
    ):
        return InterestBucket.MEDIUM
    if long_score >= params.bucket_low or short_score >= params.bucket_low:
        return InterestBucket.LOW
    return InterestBucket.NEUTRAL


_BUCKET_ORDER: dict[InterestBucket, int] = {
    InterestBucket.HIGH: 0,
    InterestBucket.MEDIUM: 1,
    InterestBucket.LOW: 2,
    InterestBucket.NEUTRAL: 3,
}


def bucket_sort_key(bucket: InterestBucket) -> int:
    """/interest/state 정렬용 — HIGH=0 → NEUTRAL=3."""
    return _BUCKET_ORDER[bucket]


__all__ = ["bucket_for", "bucket_sort_key"]
