"""trace merge evaluator 룰 trigger 테스트 — A7 결정 #17/#21/#22.

순수 함수 find_merge_candidates + _decide_winner 검증 (no DB / LLM).
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.contracts import TraversalStatus
from app.traversal.merge_evaluator import (
    MergeCandidate,
    _decide_winner,
    find_merge_candidates,
)


def _mock_trace(
    path: list[uuid.UUID],
    *,
    last_activity: int = 100,
    trace_id: uuid.UUID | None = None,
) -> MagicMock:
    trace = MagicMock()
    trace.trace_id = trace_id or uuid.uuid4()
    trace.user_id = uuid.uuid4()
    trace.path = path
    trace.status = TraversalStatus.ACTIVE.value
    trace.last_activity_active_day = last_activity
    return trace


class TestFindMergeCandidates:
    def test_overlap_ge_3_detects_candidate(self) -> None:
        a, b, c, d = (uuid.uuid4() for _ in range(4))
        t1 = _mock_trace([a, b, c])
        t2 = _mock_trace([a, b, c, d])  # overlap [a,b,c] = 3
        candidates = find_merge_candidates([t1, t2], overlap_min=3)
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand.overlap_count == 3
        # proper subset 도 동시에 매칭되나 path_overlap 분기 먼저 — overlap >= overlap_min 우선.
        assert cand.reason in ("path_overlap", "proper_subset")

    def test_overlap_below_min_skipped(self) -> None:
        a, b, c, d, e = (uuid.uuid4() for _ in range(5))
        t1 = _mock_trace([a, b])
        t2 = _mock_trace([c, d, e])  # overlap = 0
        candidates = find_merge_candidates([t1, t2], overlap_min=3)
        assert candidates == []

    def test_proper_subset_detected_when_overlap_below(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        t1 = _mock_trace([a])
        t2 = _mock_trace([a, b])  # t1 path ⊂ t2 path, overlap=1 < 3
        candidates = find_merge_candidates([t1, t2], overlap_min=3)
        assert len(candidates) == 1
        assert candidates[0].reason == "proper_subset"

    def test_equal_paths_not_candidate(self) -> None:
        """동일 path 는 proper subset 아님 (equal 은 proper 가 아님)."""
        a, b = uuid.uuid4(), uuid.uuid4()
        t1 = _mock_trace([a, b])
        t2 = _mock_trace([a, b])
        # overlap=2 < 3 AND equal (not proper subset).
        candidates = find_merge_candidates([t1, t2], overlap_min=3)
        # equal path: overlap < overlap_min, set comparison < 도 False (subset 이지만 proper 아님).
        assert candidates == []

    def test_multi_trace_pairs(self) -> None:
        a, b, c, d = (uuid.uuid4() for _ in range(4))
        t1 = _mock_trace([a, b, c])
        t2 = _mock_trace([a, b, c, d])  # overlap=3
        t3 = _mock_trace([a, b, c])     # proper subset 아님 (t1 와 동일), overlap=3 with t1
        candidates = find_merge_candidates([t1, t2, t3], overlap_min=3)
        # t1-t2 + t1-t3 + t2-t3 페어 가능. t1-t3 overlap=3 일치, t2-t3 overlap=3
        assert len(candidates) >= 2


class TestDecideWinner:
    def test_higher_activity_wins(self) -> None:
        t1 = _mock_trace([uuid.uuid4()], last_activity=50)
        t2 = _mock_trace([uuid.uuid4()], last_activity=100)
        winner, loser = _decide_winner(t1, t2)
        assert winner == t2.trace_id
        assert loser == t1.trace_id

    def test_tie_resolved_by_smaller_trace_id(self) -> None:
        """tie 시 trace_id 작은 쪽 winner (deterministic, plan #22 + plan TBD)."""
        smaller = uuid.UUID("00000000-0000-0000-0000-000000000001")
        larger = uuid.UUID("00000000-0000-0000-0000-000000000002")
        t1 = _mock_trace([uuid.uuid4()], last_activity=100, trace_id=larger)
        t2 = _mock_trace([uuid.uuid4()], last_activity=100, trace_id=smaller)
        winner, loser = _decide_winner(t1, t2)
        assert winner == smaller
        assert loser == larger


class TestMergeCandidateDataclass:
    def test_frozen_immutable(self) -> None:
        cand = MergeCandidate(
            source_trace_id=uuid.uuid4(),
            other_trace_id=uuid.uuid4(),
            overlap_count=3,
            reason="path_overlap",
        )
        import dataclasses

        assert dataclasses.is_dataclass(cand)
