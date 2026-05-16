"""bucket_for 경계 매트릭스 — interest-bayesian.md §3 표 그대로.

| 조건 | bucket |
|---|---|
| long ≥ 0.7 AND short ≥ 0.6 | HIGH |
| long ≥ 0.5 OR short ≥ 0.5 | MEDIUM |
| long ≥ 0.3 OR short ≥ 0.3 | LOW |
| 그 외 | NEUTRAL |
"""
from __future__ import annotations

import pytest

from app.contracts import InterestBucket
from app.interest.bucket import bucket_for, bucket_sort_key
from app.interest.config_loader import InterestParams


@pytest.fixture
def params() -> InterestParams:
    return InterestParams(
        alpha_prior=1.0,
        beta_prior=4.0,
        half_life_short_active_days=7,
        half_life_long_active_days=60,
        onboarding_prior_boost=1.0,
        onboarding_boost_active_days=14,
        propagation_hop_decay=0.5,
        propagation_max_hops=4,
        propagation_non_trace_ancestors=False,
        bucket_high_long=0.70,
        bucket_high_short=0.60,
        bucket_medium=0.50,
        bucket_low=0.30,
    )


class TestBucketBoundaries:
    def test_high_exactly_on_threshold(self, params: InterestParams) -> None:
        assert bucket_for(0.70, 0.60, params) == InterestBucket.HIGH

    def test_high_long_below_threshold(self, params: InterestParams) -> None:
        # long 0.69 → HIGH 미달, 0.69>=0.5 → MEDIUM
        assert bucket_for(0.69, 0.99, params) == InterestBucket.MEDIUM

    def test_high_short_below_threshold(self, params: InterestParams) -> None:
        # long 0.99 (>=0.7) AND short 0.59 (<0.6) → HIGH 미달, short>=0.5 → MEDIUM
        assert bucket_for(0.99, 0.59, params) == InterestBucket.MEDIUM

    def test_medium_via_long(self, params: InterestParams) -> None:
        assert bucket_for(0.5, 0.2, params) == InterestBucket.MEDIUM

    def test_medium_via_short(self, params: InterestParams) -> None:
        assert bucket_for(0.2, 0.5, params) == InterestBucket.MEDIUM

    def test_low_via_long(self, params: InterestParams) -> None:
        assert bucket_for(0.3, 0.1, params) == InterestBucket.LOW

    def test_low_via_short(self, params: InterestParams) -> None:
        assert bucket_for(0.1, 0.3, params) == InterestBucket.LOW

    def test_neutral_below_all(self, params: InterestParams) -> None:
        assert bucket_for(0.1, 0.1, params) == InterestBucket.NEUTRAL

    def test_neutral_zero_zero(self, params: InterestParams) -> None:
        assert bucket_for(0.0, 0.0, params) == InterestBucket.NEUTRAL


class TestBucketSortKey:
    def test_high_to_neutral_ordering(self) -> None:
        assert (
            bucket_sort_key(InterestBucket.HIGH)
            < bucket_sort_key(InterestBucket.MEDIUM)
            < bucket_sort_key(InterestBucket.LOW)
            < bucket_sort_key(InterestBucket.NEUTRAL)
        )
