"""recommendation.toml 로더 — `RecommendationConfig` dataclass.

recommendation-ranking.md §신뢰도 임계 + §다양성 룰 + §core_slot_quota SOR.
lifespan 부팅 시 1회 로드 (모듈 로드 시점에 lazy parse). 운영 시점 갱신은 backend 재시작.

Settings env (RECOMMENDATION_CACHE_TTL_SECONDS 등) 와 본 TOML 파일은 책임 분리:
- Settings env: runtime tuning (TTL, timeout, cap)
- recommendation.toml: 알고리즘 임계 (점수 weight, 슬롯 target, fallback 단계)
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SlotTargets:
    core: int
    adjacent: int
    discovery: int
    total: int


@dataclass(frozen=True, slots=True)
class ConfidenceThresholds:
    core_min_topic_match: float
    adjacent_min_topic_match: float
    discovery_min_topic_match: float
    discovery_required_trust_level: str


@dataclass(frozen=True, slots=True)
class FreshnessConfig:
    fresh_full_hours: int
    fresh_floor_after_wallclock_days: int
    fresh_floor_value: float


@dataclass(frozen=True, slots=True)
class TrustLevelWeights:
    high: float
    medium: float
    low: float

    def lookup(self, level: str) -> float:
        """trust_level 문자열 → 가중치. unknown 은 medium 로 fallback."""
        if level == "high":
            return self.high
        if level == "medium":
            return self.medium
        if level == "low":
            return self.low
        return self.medium


@dataclass(frozen=True, slots=True)
class RankingWeights:
    w_match: float
    w_fresh: float
    w_trust: float


@dataclass(frozen=True, slots=True)
class DiversificationConfig:
    max_per_source_in_slot: int
    max_per_leaf_in_slot: int


@dataclass(frozen=True, slots=True)
class CoreSlotQuota:
    emerging_leaf_quota_in_core: int


@dataclass(frozen=True, slots=True)
class BucketScoreWeights:
    high: float
    medium: float
    low: float
    neutral: float

    def lookup(self, bucket: str) -> float:
        """bucket 문자열 → 가중치. unknown 은 neutral fallback."""
        if bucket == "high":
            return self.high
        if bucket == "medium":
            return self.medium
        if bucket == "low":
            return self.low
        return self.neutral


@dataclass(frozen=True, slots=True)
class FallbackConfig:
    adjacent_hops_2: int
    trend_window_days: int
    archive_window_days: int


@dataclass(frozen=True, slots=True)
class RecommendationConfig:
    slot_targets: SlotTargets
    confidence_thresholds: ConfidenceThresholds
    freshness: FreshnessConfig
    trust_level_weights: TrustLevelWeights
    ranking_weights: RankingWeights
    diversification: DiversificationConfig
    core_slot_quota: CoreSlotQuota
    bucket_score: BucketScoreWeights
    fallback: FallbackConfig


_RECOMMENDATION_TOML_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "recommendation.toml"
)


def _parse(raw: dict[str, object]) -> RecommendationConfig:
    """tomllib dict → RecommendationConfig dataclass."""
    s = raw["slot_targets"]
    if not isinstance(s, dict):
        raise ValueError("recommendation.toml: [slot_targets] missing or invalid")
    c = raw["confidence_thresholds"]
    if not isinstance(c, dict):
        raise ValueError(
            "recommendation.toml: [confidence_thresholds] missing or invalid"
        )
    f = raw["freshness"]
    if not isinstance(f, dict):
        raise ValueError("recommendation.toml: [freshness] missing or invalid")
    t = raw["trust_level_weights"]
    if not isinstance(t, dict):
        raise ValueError(
            "recommendation.toml: [trust_level_weights] missing or invalid"
        )
    r = raw["ranking_weights"]
    if not isinstance(r, dict):
        raise ValueError(
            "recommendation.toml: [ranking_weights] missing or invalid"
        )
    d = raw["diversification"]
    if not isinstance(d, dict):
        raise ValueError(
            "recommendation.toml: [diversification] missing or invalid"
        )
    q = raw["core_slot_quota"]
    if not isinstance(q, dict):
        raise ValueError(
            "recommendation.toml: [core_slot_quota] missing or invalid"
        )
    b = raw["bucket_score"]
    if not isinstance(b, dict):
        raise ValueError("recommendation.toml: [bucket_score] missing or invalid")
    fb = raw["fallback"]
    if not isinstance(fb, dict):
        raise ValueError("recommendation.toml: [fallback] missing or invalid")
    return RecommendationConfig(
        slot_targets=SlotTargets(
            core=int(s["core"]),
            adjacent=int(s["adjacent"]),
            discovery=int(s["discovery"]),
            total=int(s["total"]),
        ),
        confidence_thresholds=ConfidenceThresholds(
            core_min_topic_match=float(c["core_min_topic_match"]),
            adjacent_min_topic_match=float(c["adjacent_min_topic_match"]),
            discovery_min_topic_match=float(c["discovery_min_topic_match"]),
            discovery_required_trust_level=str(
                c["discovery_required_trust_level"]
            ),
        ),
        freshness=FreshnessConfig(
            fresh_full_hours=int(f["fresh_full_hours"]),
            fresh_floor_after_wallclock_days=int(
                f["fresh_floor_after_wallclock_days"]
            ),
            fresh_floor_value=float(f["fresh_floor_value"]),
        ),
        trust_level_weights=TrustLevelWeights(
            high=float(t["high"]),
            medium=float(t["medium"]),
            low=float(t["low"]),
        ),
        ranking_weights=RankingWeights(
            w_match=float(r["w_match"]),
            w_fresh=float(r["w_fresh"]),
            w_trust=float(r["w_trust"]),
        ),
        diversification=DiversificationConfig(
            max_per_source_in_slot=int(d["max_per_source_in_slot"]),
            max_per_leaf_in_slot=int(d["max_per_leaf_in_slot"]),
        ),
        core_slot_quota=CoreSlotQuota(
            emerging_leaf_quota_in_core=int(q["emerging_leaf_quota_in_core"]),
        ),
        bucket_score=BucketScoreWeights(
            high=float(b["high"]),
            medium=float(b["medium"]),
            low=float(b["low"]),
            neutral=float(b["neutral"]),
        ),
        fallback=FallbackConfig(
            adjacent_hops_2=int(fb["adjacent_hops_2"]),
            trend_window_days=int(fb["trend_window_days"]),
            archive_window_days=int(fb["archive_window_days"]),
        ),
    )


@lru_cache(maxsize=1)
def get_recommendation_config() -> RecommendationConfig:
    """캐시된 RecommendationConfig. 최초 호출 시 recommendation.toml parse + dataclass build."""
    with _RECOMMENDATION_TOML_PATH.open("rb") as fh:
        raw = tomllib.load(fh)
    return _parse(raw)


__all__ = [
    "BucketScoreWeights",
    "ConfidenceThresholds",
    "CoreSlotQuota",
    "DiversificationConfig",
    "FallbackConfig",
    "FreshnessConfig",
    "RankingWeights",
    "RecommendationConfig",
    "SlotTargets",
    "TrustLevelWeights",
    "get_recommendation_config",
]
