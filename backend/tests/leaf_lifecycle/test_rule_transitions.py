"""leaf 룰 기반 전이 매트릭스 — leaf-topic-lifecycle.md L31-159 의사 코드.

전이 6종 (rule_evaluator.py):
- emerging → active   (window 7d + docs 5 + interest 2) 승격
- emerging → archived (idle 14d) 강등
- active   → stale    (idle 21d) 강등
- stale    → active   (window 7d + docs 3 + interest 1) 재활성화
- stale    → archived (idle 90d) 강등

순수 함수 evaluate_rule_transitions 호출 (no DB).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.contracts import LeafTopicStatus
from app.leaf_lifecycle.protocol import LifecycleSignals
from app.leaf_lifecycle.rule_evaluator import evaluate_rule_transitions


def _mock_leaf(status: str, leaf_id: uuid.UUID | None = None) -> MagicMock:
    """DynamicLeafTopic mock — 룰 평가에 필요한 attribute 만."""
    leaf = MagicMock()
    leaf.leaf_topic_id = leaf_id or uuid.uuid4()
    leaf.status = status
    leaf.last_signal_active_day = 0
    leaf.created_active_day = 0
    leaf.created_at = datetime.now(UTC)
    return leaf


class TestEmergingPromotion:
    def test_promote_when_window_threshold_met(self) -> None:
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(LeafTopicStatus.EMERGING.value, leaf_id)
        signals = LifecycleSignals(
            documents_in_window_7d={leaf_id: 5},     # >= LEAF_ACTIVE_MIN_DOCUMENTS
            interest_signals_in_window_7d={leaf_id: 2},  # >= LEAF_ACTIVE_MIN_INTEREST_SIGNALS
            idle_active_days={leaf_id: 3},
        )
        transitions = evaluate_rule_transitions([leaf], signals)
        assert len(transitions) == 1
        t = transitions[0]
        assert t.leaf_topic_id == leaf_id
        assert t.from_status == LeafTopicStatus.EMERGING.value
        assert t.to_status == LeafTopicStatus.ACTIVE.value
        assert t.reason == "window_promotion"

    def test_no_promotion_when_documents_below_threshold(self) -> None:
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(LeafTopicStatus.EMERGING.value, leaf_id)
        signals = LifecycleSignals(
            documents_in_window_7d={leaf_id: 4},  # 5 미만
            interest_signals_in_window_7d={leaf_id: 2},
            idle_active_days={leaf_id: 3},
        )
        assert evaluate_rule_transitions([leaf], signals) == []

    def test_no_promotion_when_interest_below_threshold(self) -> None:
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(LeafTopicStatus.EMERGING.value, leaf_id)
        signals = LifecycleSignals(
            documents_in_window_7d={leaf_id: 5},
            interest_signals_in_window_7d={leaf_id: 1},  # 2 미만
            idle_active_days={leaf_id: 3},
        )
        assert evaluate_rule_transitions([leaf], signals) == []


class TestEmergingArchive:
    def test_archive_when_idle_14d_no_window(self) -> None:
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(LeafTopicStatus.EMERGING.value, leaf_id)
        signals = LifecycleSignals(
            documents_in_window_7d={leaf_id: 0},
            interest_signals_in_window_7d={leaf_id: 0},
            idle_active_days={leaf_id: 14},
        )
        transitions = evaluate_rule_transitions([leaf], signals)
        assert len(transitions) == 1
        assert transitions[0].to_status == LeafTopicStatus.ARCHIVED.value
        assert transitions[0].reason == "emerging_idle_archived"


class TestActiveDemotion:
    def test_demote_to_stale_at_21d(self) -> None:
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(LeafTopicStatus.ACTIVE.value, leaf_id)
        signals = LifecycleSignals(idle_active_days={leaf_id: 21})
        transitions = evaluate_rule_transitions([leaf], signals)
        assert len(transitions) == 1
        assert transitions[0].to_status == LeafTopicStatus.STALE.value
        assert transitions[0].reason == "idle_demotion"

    def test_no_demotion_when_idle_below_21d(self) -> None:
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(LeafTopicStatus.ACTIVE.value, leaf_id)
        signals = LifecycleSignals(idle_active_days={leaf_id: 20})
        assert evaluate_rule_transitions([leaf], signals) == []


class TestStaleTransitions:
    def test_reactivate_to_active(self) -> None:
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(LeafTopicStatus.STALE.value, leaf_id)
        signals = LifecycleSignals(
            documents_in_window_7d={leaf_id: 3},      # >= LEAF_REACTIVATION_MIN_DOCUMENTS
            interest_signals_in_window_7d={leaf_id: 1},  # >= LEAF_REACTIVATION_MIN_INTEREST_SIGNALS
            idle_active_days={leaf_id: 30},
        )
        transitions = evaluate_rule_transitions([leaf], signals)
        assert len(transitions) == 1
        assert transitions[0].to_status == LeafTopicStatus.ACTIVE.value
        assert transitions[0].reason == "reactivation"

    def test_archive_when_idle_90d(self) -> None:
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(LeafTopicStatus.STALE.value, leaf_id)
        signals = LifecycleSignals(
            documents_in_window_7d={leaf_id: 0},
            interest_signals_in_window_7d={leaf_id: 0},
            idle_active_days={leaf_id: 90},
        )
        transitions = evaluate_rule_transitions([leaf], signals)
        assert len(transitions) == 1
        assert transitions[0].to_status == LeafTopicStatus.ARCHIVED.value
        assert transitions[0].reason == "stale_archived"

    def test_reactivation_takes_priority_over_archive(self) -> None:
        """idle 100d 이면서 window 충족 시 → reactivation 우선 (archive 안 됨)."""
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(LeafTopicStatus.STALE.value, leaf_id)
        signals = LifecycleSignals(
            documents_in_window_7d={leaf_id: 3},
            interest_signals_in_window_7d={leaf_id: 1},
            idle_active_days={leaf_id: 100},
        )
        transitions = evaluate_rule_transitions([leaf], signals)
        assert len(transitions) == 1
        assert transitions[0].to_status == LeafTopicStatus.ACTIVE.value


class TestMergedArchivedIgnored:
    """merged / archived 는 본 함수에서 더 이상 전이 안 함."""

    @pytest.mark.parametrize(
        "status",
        [LeafTopicStatus.MERGED.value, LeafTopicStatus.ARCHIVED.value],
    )
    def test_no_transitions(self, status: str) -> None:
        leaf_id = uuid.uuid4()
        leaf = _mock_leaf(status, leaf_id)
        signals = LifecycleSignals(idle_active_days={leaf_id: 1000})
        assert evaluate_rule_transitions([leaf], signals) == []
