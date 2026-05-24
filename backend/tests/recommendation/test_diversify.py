"""diversify cap — max_per_source / max_per_leaf."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.recommendation.config_loader import DiversificationConfig
from app.recommendation.diversify import diversify
from app.recommendation.ranking import ScoredCandidate


def _make_card(
    *,
    document_id: uuid.UUID | None = None,
    source_id: uuid.UUID,
    source_name: str = "src",
    leaf_topic_id: uuid.UUID | None = None,
    score: float = 0.8,
) -> ScoredCandidate:
    return ScoredCandidate(
        document_id=document_id or uuid.uuid4(),
        title="t",
        source_id=source_id,
        source_name=source_name,
        source_type="vendor_blog",
        trust_level="high",
        published_at=datetime.now(UTC),
        cso_topic_id=uuid.uuid4(),
        leaf_topic_id=leaf_topic_id,
        leaf_status=None,
        leaf_label=None,
        cso_label="topic",
        topic_confidence=0.9,
        topic_match=0.8,
        freshness=1.0,
        trust=1.0,
        score=score,
    )


def test_max_per_source_caps_at_two() -> None:
    src_a = uuid.uuid4()
    src_b = uuid.uuid4()
    cards = [
        _make_card(source_id=src_a, score=0.9),
        _make_card(source_id=src_a, score=0.8),
        _make_card(source_id=src_a, score=0.7),    # cap 초과 — drop
        _make_card(source_id=src_b, score=0.6),
    ]
    cfg = DiversificationConfig(
        max_per_source_in_slot=2, max_per_leaf_in_slot=3
    )
    result = diversify(cards, cfg)
    src_counts: dict[uuid.UUID, int] = {}
    for c in result:
        src_counts[c.source_id] = src_counts.get(c.source_id, 0) + 1
    assert src_counts[src_a] == 2
    assert src_counts[src_b] == 1


def test_max_per_leaf_caps_at_three() -> None:
    leaf = uuid.uuid4()
    cards = [
        _make_card(source_id=uuid.uuid4(), leaf_topic_id=leaf, score=0.9),
        _make_card(source_id=uuid.uuid4(), leaf_topic_id=leaf, score=0.8),
        _make_card(source_id=uuid.uuid4(), leaf_topic_id=leaf, score=0.7),
        _make_card(source_id=uuid.uuid4(), leaf_topic_id=leaf, score=0.6),   # cap 초과 — drop
    ]
    cfg = DiversificationConfig(
        max_per_source_in_slot=10, max_per_leaf_in_slot=3
    )
    result = diversify(cards, cfg)
    leaf_count = sum(1 for c in result if c.leaf_topic_id == leaf)
    assert leaf_count == 3


def test_leaf_none_bypasses_leaf_cap() -> None:
    """leaf_topic_id=None 후보는 leaf cap 면제 (cso-only 매핑)."""
    cards = [
        _make_card(source_id=uuid.uuid4(), leaf_topic_id=None, score=0.9),
        _make_card(source_id=uuid.uuid4(), leaf_topic_id=None, score=0.8),
        _make_card(source_id=uuid.uuid4(), leaf_topic_id=None, score=0.7),
        _make_card(source_id=uuid.uuid4(), leaf_topic_id=None, score=0.6),
    ]
    cfg = DiversificationConfig(
        max_per_source_in_slot=10, max_per_leaf_in_slot=1
    )
    result = diversify(cards, cfg)
    # leaf=None 4개 모두 통과 — leaf cap 면제.
    assert len(result) == 4


def test_llm_search_sentinel_bypasses_source_cap() -> None:
    """llm_search 는 실제 publisher 가 아니라 sentinel source 라 source cap 면제."""
    src = uuid.uuid4()
    cards = [
        _make_card(source_id=src, source_name="llm_search", score=0.9),
        _make_card(source_id=src, source_name="llm_search", score=0.8),
        _make_card(source_id=src, source_name="llm_search", score=0.7),
        _make_card(source_id=src, source_name="llm_search", score=0.6),
    ]
    cfg = DiversificationConfig(
        max_per_source_in_slot=2, max_per_leaf_in_slot=10
    )
    result = diversify(cards, cfg)
    assert len(result) == 4


def test_preserves_score_order() -> None:
    src = uuid.uuid4()
    cards = [
        _make_card(source_id=src, score=0.9),
        _make_card(source_id=src, score=0.8),
        _make_card(source_id=uuid.uuid4(), score=0.7),
    ]
    cfg = DiversificationConfig(
        max_per_source_in_slot=2, max_per_leaf_in_slot=3
    )
    result = diversify(cards, cfg)
    assert [c.score for c in result] == [0.9, 0.8, 0.7]
