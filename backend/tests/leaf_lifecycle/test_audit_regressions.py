"""C-56 leaf promotion ingest hook 회귀 가드 (정적 source inspection).

A7 P1-12 fix (trace extend/split caller 부재) 와 같은 패턴의 결함이 leaf 영역에 잔존했던
것을 C-56 라운드에서 fix. 본 테스트는 production caller 가 다시 누락되지 않도록 가드.

검증:
1. interest/service.py 의 ingest_event_atomic 안에 leaf hook (_update_leaf_last_signal +
   _evaluate_leaf_promotion) 호출 존재.
2. _LEAF_PROMOTION_EVENT_TYPES 가 click/save/dwell_tick 3종 포함 (사용자 결정 "넓게").
3. _evaluate_leaf_promotion 이 evaluate_rule_transitions + apply_transitions 호출 +
   promotions filter (window_promotion / reactivation reason 만).
4. _update_leaf_last_signal 이 status emerging/active/stale 필터 + last_signal_active_day
   UPDATE.
"""
from __future__ import annotations

import inspect

from app.contracts import EventType
from app.interest import service


def _source(obj: object) -> str:
    return inspect.getsource(obj)


class TestC56LeafPromotionIngestHook:
    """leaf 활성 신호 ingest hook caller 존재 가드."""

    def test_ingest_event_atomic_calls_update_leaf_last_signal(self) -> None:
        src = _source(service.ingest_event_atomic)
        assert "_update_leaf_last_signal" in src

    def test_ingest_event_atomic_calls_evaluate_leaf_promotion(self) -> None:
        src = _source(service.ingest_event_atomic)
        assert "_evaluate_leaf_promotion" in src

    def test_promotion_event_types_covers_click_save_dwell_tick(self) -> None:
        """사용자 결정 (C-56) — 넓게: click + save + dwell_tick."""
        types = service._LEAF_PROMOTION_EVENT_TYPES
        assert EventType.CLICK.value in types
        assert EventType.SAVE.value in types
        assert EventType.DWELL_TICK.value in types

    def test_update_leaf_last_signal_filters_active_statuses(self) -> None:
        src = _source(service._update_leaf_last_signal)
        # emerging/active/stale 만 갱신 대상 — merged/archived 는 제외.
        assert "EMERGING" in src and "ACTIVE" in src and "STALE" in src
        assert "last_signal_active_day" in src

    def test_evaluate_leaf_promotion_applies_only_promotions(self) -> None:
        """강등은 daily cron 책임 — 본 hook 은 promotion 만 apply."""
        src = _source(service._evaluate_leaf_promotion)
        assert "evaluate_rule_transitions" in src
        assert "apply_transitions" in src
        assert "window_promotion" in src
        assert "reactivation" in src
        # 강등 reason 은 본 hook 에서 apply 안 함.
        assert "idle_demotion" not in src
        assert "stale_archived" not in src
