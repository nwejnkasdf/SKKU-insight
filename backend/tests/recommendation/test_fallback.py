"""FR-42 (slot 부족) + FR-43 (전체 부족) fallback."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.contracts import SlotType
from app.recommendation.config_loader import (
    ConfidenceThresholds,
    CoreSlotQuota,
    SlotTargets,
)
from app.recommendation.fallback import fill_slots
from app.recommendation.ranking import ScoredCandidate

_TARGETS = SlotTargets(core=5, adjacent=3, discovery=2, total=10)
_THRESHOLDS = ConfidenceThresholds(
    core_min_topic_match=0.75,
    adjacent_min_topic_match=0.55,
    discovery_min_topic_match=0.30,
    discovery_required_trust_level="high",
)
_QUOTA = CoreSlotQuota(emerging_leaf_quota_in_core=1)


def _card(
    *,
    trust: str = "high",
    topic_match: float = 0.9,
    topic_confidence: float | None = None,
) -> ScoredCandidate:
    confidence = topic_match if topic_confidence is None else topic_confidence
    return ScoredCandidate(
        document_id=uuid.uuid4(),
        title="t",
        source_id=uuid.uuid4(),
        source_name="src",
        source_type="vendor_blog",
        trust_level=trust,
        published_at=datetime.now(UTC),
        cso_topic_id=uuid.uuid4(),
        leaf_topic_id=None,
        leaf_status=None,
        leaf_label=None,
        cso_label="t",
        topic_confidence=confidence,
        topic_match=topic_match,
        freshness=1.0,
        trust=1.0,
        score=topic_match + 0.2,
    )


def test_fr42_slot_short_borrows_from_donor() -> None:
    """core 부족 시 adjacent 잉여 (core 임계 통과 0.75+) 로 보충."""
    core_pool = [_card(topic_match=0.9) for _ in range(2)]
    # adjacent 6개 — 3 target 채우고 3 잉여 (모두 0.85, core 임계 통과)
    adjacent_pool = [_card(topic_match=0.85) for _ in range(6)]
    discovery_pool = [_card(topic_match=0.5) for _ in range(2)]
    filled = fill_slots(
        core_pool=core_pool,
        adjacent_pool=adjacent_pool,
        discovery_pool=discovery_pool,
        emerging_pool=[],
        targets=_TARGETS,
        thresholds=_THRESHOLDS,
        quota=_QUOTA,
    )
    assert len(filled.core) == 5     # 2 + 3 fallback
    # fallback_reason 기록
    assert filled.fallback_reasons.get(SlotType.CORE) is not None
    assert "slot_core_short_by_3" in filled.fallback_reasons[SlotType.CORE]


def test_fr42_low_threshold_not_forced() -> None:
    """저신뢰 후보 (core 임계 0.75 미만) 로 core 강제 X."""
    core_pool = [_card(topic_match=0.9) for _ in range(2)]
    # adjacent 5개 — 모두 0.5, core 임계 미통과
    adjacent_pool = [_card(topic_match=0.5) for _ in range(5)]
    discovery_pool = [_card(topic_match=0.5) for _ in range(2)]
    filled = fill_slots(
        core_pool=core_pool,
        adjacent_pool=adjacent_pool,
        discovery_pool=discovery_pool,
        emerging_pool=[],
        targets=_TARGETS,
        thresholds=_THRESHOLDS,
        quota=_QUOTA,
    )
    # core 는 2개만 — adjacent 잉여는 임계 미달이라 가져오지 않음.
    assert len(filled.core) == 2
    # fallback_reason 기록 (전체 부족 표시).
    assert filled.fallback_reasons.get(SlotType.CORE) is not None


def test_emerging_quota_filled_first() -> None:
    """core 5 중 1개는 emerging quota 우선."""
    core_pool = [_card(topic_match=0.9) for _ in range(10)]
    emerging_pool = [_card(topic_match=0.9) for _ in range(3)]
    filled = fill_slots(
        core_pool=core_pool,
        adjacent_pool=[],
        discovery_pool=[],
        emerging_pool=emerging_pool,
        targets=_TARGETS,
        thresholds=_THRESHOLDS,
        quota=_QUOTA,
    )
    # 첫 카드는 emerging_pool 출신 (quota 1).
    assert filled.core[0] in emerging_pool
    # 나머지 4 는 core_pool.
    assert len([c for c in filled.core if c in emerging_pool]) == 1
    assert len([c for c in filled.core if c in core_pool]) == 4


def test_emerging_quota_recovers_to_active_when_absent() -> None:
    """emerging_pool 비어 있으면 core 5 전부 core_pool 에서."""
    core_pool = [_card(topic_match=0.9) for _ in range(10)]
    filled = fill_slots(
        core_pool=core_pool,
        adjacent_pool=[],
        discovery_pool=[],
        emerging_pool=[],
        targets=_TARGETS,
        thresholds=_THRESHOLDS,
        quota=_QUOTA,
    )
    assert len(filled.core) == 5
    for c in filled.core:
        assert c in core_pool


def test_discovery_requires_trust_high() -> None:
    """discovery 는 trust_level='high' 만 통과."""
    discovery_pool = [
        _card(topic_match=0.5, trust="medium"),
        _card(topic_match=0.5, trust="high"),
        _card(topic_match=0.5, trust="low"),
    ]
    filled = fill_slots(
        core_pool=[],
        adjacent_pool=[],
        discovery_pool=discovery_pool,
        emerging_pool=[],
        targets=_TARGETS,
        thresholds=_THRESHOLDS,
        quota=_QUOTA,
    )
    # high 만 1개 통과.
    assert len(filled.discovery) == 1
    assert filled.discovery[0].trust_level == "high"


def test_adjacent_threshold_uses_topic_confidence_not_bucketed_match() -> None:
    """인접 슬롯은 낮은 bucket 점수여도 매핑 confidence 가 높으면 통과한다."""
    adjacent_pool = [
        _card(topic_match=0.32, topic_confidence=0.86),
        _card(topic_match=0.34, topic_confidence=0.88),
        _card(topic_match=0.36, topic_confidence=0.90),
    ]
    filled = fill_slots(
        core_pool=[],
        adjacent_pool=adjacent_pool,
        discovery_pool=[],
        emerging_pool=[],
        targets=_TARGETS,
        thresholds=_THRESHOLDS,
        quota=_QUOTA,
    )
    assert len(filled.adjacent) == 3


def test_discovery_threshold_uses_topic_confidence_not_bucketed_match() -> None:
    """탐색 슬롯도 neutral bucket 때문에 high-confidence trend 를 탈락시키지 않는다."""
    discovery_pool = [
        _card(trust="high", topic_match=0.18, topic_confidence=0.90),
        _card(trust="high", topic_match=0.16, topic_confidence=0.80),
    ]
    filled = fill_slots(
        core_pool=[],
        adjacent_pool=[],
        discovery_pool=discovery_pool,
        emerging_pool=[],
        targets=_TARGETS,
        thresholds=_THRESHOLDS,
        quota=_QUOTA,
    )
    assert len(filled.discovery) == 2
