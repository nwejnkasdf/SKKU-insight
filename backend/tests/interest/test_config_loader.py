"""config_loader.InterestParams + EventWeights from_dict — DB X."""
from __future__ import annotations

import pytest

from app.interest.config_loader import EventWeights, InterestParams


def test_interest_params_from_dict() -> None:
    raw = {
        "alpha_prior": 1.0,
        "beta_prior": 4.0,
        "half_life_short_active_days": 7,
        "half_life_long_active_days": 60,
        "onboarding_prior_boost": 1.0,
        "onboarding_boost_active_days": 14,
        "propagation_hop_decay": 0.5,
        "propagation_max_hops": 4,
        "propagation_non_trace_ancestors": False,
        "bucket_high_long": 0.70,
        "bucket_high_short": 0.60,
        "bucket_medium": 0.50,
        "bucket_low": 0.30,
    }
    p = InterestParams.from_dict(raw)
    assert p.alpha_prior == 1.0
    assert p.beta_prior == 4.0
    assert p.half_life_short_active_days == 7
    assert p.half_life_long_active_days == 60
    assert p.onboarding_prior_boost == 1.0
    assert p.onboarding_boost_active_days == 14
    assert p.propagation_hop_decay == 0.5
    assert p.propagation_max_hops == 4
    assert p.propagation_non_trace_ancestors is False
    assert p.bucket_high_long == 0.70
    assert p.bucket_high_short == 0.60
    assert p.bucket_medium == 0.50
    assert p.bucket_low == 0.30


def test_event_weights_from_dict() -> None:
    raw = {
        "weights": {
            "view": 0.0,
            "click": 1.0,
            "dwell_tick": 0.5,
            "open_external": 2.0,
            "save": 5.0,
            "hide": -3.0,
            "not_interested": -5.0,
        },
        "caps": {
            "dwell_tick_max_per_document": 4,
            "weight_per_event_max": 5.0,
        },
    }
    w = EventWeights.from_dict(raw)
    assert w.lookup("click") == 1.0
    assert w.lookup("save") == 5.0
    assert w.lookup("hide") == -3.0
    assert w.lookup("not_interested") == -5.0
    assert w.lookup("view") == 0.0
    assert w.lookup("unknown") == 0.0
    assert w.dwell_tick_max_per_document == 4
    assert w.weight_per_event_max == 5.0


def test_event_weights_caps_defaults_when_missing() -> None:
    raw = {"weights": {"click": 1.0}}
    w = EventWeights.from_dict(raw)
    assert w.lookup("click") == 1.0
    # caps 누락 시 default
    assert w.dwell_tick_max_per_document == 4
    assert w.weight_per_event_max == 5.0


def test_interest_params_missing_required_raises() -> None:
    raw = {"alpha_prior": 1.0}  # 나머지 누락
    with pytest.raises(KeyError):
        InterestParams.from_dict(raw)
