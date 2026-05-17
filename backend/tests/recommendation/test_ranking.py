"""ranking — score 산출 + topic_match dedup + freshness wallclock."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.interest.config_loader import InterestParams
from app.recommendation.candidates import CandidateRow
from app.recommendation.config_loader import (
    BucketScoreWeights,
    FreshnessConfig,
    RankingWeights,
    TrustLevelWeights,
)
from app.recommendation.ranking import score_candidates

_PARAMS = InterestParams(
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
_RANKING = RankingWeights(w_match=0.7, w_fresh=0.2, w_trust=0.1)
_FRESH = FreshnessConfig(
    fresh_full_hours=24, fresh_floor_after_wallclock_days=7, fresh_floor_value=0.5
)
_TRUST = TrustLevelWeights(high=1.0, medium=0.85, low=0.6)
_BUCKETS = BucketScoreWeights(high=1.0, medium=0.7, low=0.4, neutral=0.2)


def _row(
    *,
    document_id: uuid.UUID | None = None,
    cso_topic_id: uuid.UUID | None = None,
    leaf_topic_id: uuid.UUID | None = None,
    confidence: float = 0.9,
    published_at: datetime | None = None,
    trust_level: str = "high",
) -> CandidateRow:
    return CandidateRow(
        document_id=document_id or uuid.uuid4(),
        title="t",
        source_id=uuid.uuid4(),
        source_name="s",
        source_type="academic",
        trust_level=trust_level,
        published_at=published_at or datetime.now(UTC),
        cso_topic_id=cso_topic_id or uuid.uuid4(),
        leaf_topic_id=leaf_topic_id,
        leaf_status=None,
        leaf_label=None,
        cso_label="t",
        topic_confidence=confidence,
    )


def test_freshness_24h_is_one() -> None:
    """24h 이내 published_at → freshness=1.0."""
    row = _row(published_at=datetime.now(UTC) - timedelta(hours=1))
    scored = score_candidates([row], {}, _PARAMS, _RANKING, _FRESH, _TRUST, _BUCKETS)
    assert abs(scored[0].freshness - 1.0) < 0.01


def test_freshness_floor_after_7_days() -> None:
    """7d 이상 → floor (0.5)."""
    row = _row(published_at=datetime.now(UTC) - timedelta(days=14))
    scored = score_candidates([row], {}, _PARAMS, _RANKING, _FRESH, _TRUST, _BUCKETS)
    assert abs(scored[0].freshness - 0.5) < 0.01


def test_dedup_by_document_id_keeps_max_topic_match() -> None:
    """같은 document 의 다중 매핑 → max(topic_match) 만 유지."""
    doc_id = uuid.uuid4()
    rows = [
        _row(document_id=doc_id, confidence=0.3),
        _row(document_id=doc_id, confidence=0.9),
    ]
    scored = score_candidates(rows, {}, _PARAMS, _RANKING, _FRESH, _TRUST, _BUCKETS)
    assert len(scored) == 1
    # confidence 0.9 row 가 선택됨 (state_index 없으니 NEUTRAL bucket=0.2, max =0.2*0.9=0.18).
    assert scored[0].topic_match == pytest.approx(0.2 * 0.9)


def test_score_formula() -> None:
    """score = topic_match·0.7 + freshness·0.2 + trust·0.1."""
    row = _row(
        confidence=1.0,
        trust_level="high",
        published_at=datetime.now(UTC) - timedelta(hours=1),
    )
    scored = score_candidates([row], {}, _PARAMS, _RANKING, _FRESH, _TRUST, _BUCKETS)
    s = scored[0]
    expected = s.topic_match * 0.7 + s.freshness * 0.2 + s.trust * 0.1
    assert abs(s.score - expected) < 0.001


def test_state_miss_neutral_fallback() -> None:
    """UserInterestState 없으면 NEUTRAL bucket score (0.2)."""
    row = _row(confidence=1.0)
    scored = score_candidates([row], {}, _PARAMS, _RANKING, _FRESH, _TRUST, _BUCKETS)
    # NEUTRAL bucket = 0.2, confidence=1.0 → topic_match=0.2.
    assert scored[0].topic_match == pytest.approx(0.2)


def test_trust_level_weight() -> None:
    """trust=high → 1.0, medium → 0.85, low → 0.6."""
    row_high = _row(trust_level="high")
    row_med = _row(trust_level="medium")
    row_low = _row(trust_level="low")
    scored_high = score_candidates(
        [row_high], {}, _PARAMS, _RANKING, _FRESH, _TRUST, _BUCKETS
    )
    scored_med = score_candidates(
        [row_med], {}, _PARAMS, _RANKING, _FRESH, _TRUST, _BUCKETS
    )
    scored_low = score_candidates(
        [row_low], {}, _PARAMS, _RANKING, _FRESH, _TRUST, _BUCKETS
    )
    assert scored_high[0].trust == 1.0
    assert scored_med[0].trust == 0.85
    assert scored_low[0].trust == 0.6
