"""A7 본문 anti-pattern 회귀 가드 (정적 source inspection).

A6 fix 12건 (C-37/C-38) 학습 — A7 본문이 같은 anti-pattern 을 다시 도입하지 않도록 가드.
P2 백로그 (P2-17/P2-20): 정적 검사는 변수명 변경 우회 가능 — DB fixture 통합 테스트는 R3.

검증 대상 (모두 본문 source inspect):
1. trace.path mutation = atomic SQL array_append/array_remove (read-then-write 회피)
2. leaf 신규 INSERT = pg_insert.on_conflict_do_nothing + returning + None-check
3. trace merge 가 winner.last_activity_active_day 갱신
4. merged_into_trace_id 컬럼이 ORM 모델에 정의
5. TRACE_MERGE_PATH_OVERLAP_MIN 이 Settings 에 정의
"""
from __future__ import annotations

import inspect

from app.config import Settings
from app.db.models import UserCSOTraversal
from app.traversal import operations


def _source(obj: object) -> str:
    return inspect.getsource(obj)


class TestAtomicSqlMutation:
    """A6 C-01 anti-pattern (read-then-write) 회피 — atomic SQL 사용."""

    def test_execute_extend_uses_array_append(self) -> None:
        src = _source(operations.execute_extend)
        assert "array_append" in src
        # cardinality cap 검사도 SQL 내.
        assert "cardinality" in src

    def test_execute_retract_uses_array_remove(self) -> None:
        src = _source(operations.execute_retract)
        assert "array_remove" in src

    def test_execute_split_uses_pg_insert_on_conflict(self) -> None:
        src = _source(operations.execute_split)
        # A6 C-03 패턴: pg_insert + on_conflict_do_nothing + returning.
        assert "pg_insert" in src
        assert "on_conflict_do_nothing" in src
        assert "returning" in src.lower()

    def test_execute_merge_updates_winner_activity(self) -> None:
        src = _source(operations.execute_merge)
        # winner trace last_activity 갱신 (활동도 합산 의미).
        assert "last_activity_active_day" in src

    def test_execute_merge_sets_merged_into_trace_id(self) -> None:
        src = _source(operations.execute_merge)
        assert "merged_into_trace_id" in src
        # loser status='archived'.
        assert "ARCHIVED" in src or "'archived'" in src


class TestOrmModel:
    def test_merged_into_trace_id_column_defined(self) -> None:
        """alembic 0005 + ORM 미러링 (Codex round 2 S-07 패턴)."""
        col_names = {c.key for c in UserCSOTraversal.__table__.columns}
        assert "merged_into_trace_id" in col_names

    def test_partial_index_for_merged(self) -> None:
        """audit/recovery 용 partial index."""
        index_names = {idx.name for idx in UserCSOTraversal.__table__.indexes}
        assert "ix_user_cso_traversal_merged_into" in index_names


class TestSettingsConstants:
    def test_trace_merge_path_overlap_min(self) -> None:
        """A7 결정 #21 — path overlap ≥3 임계 (decision-backlog C-39)."""
        s = Settings()
        assert s.TRACE_MERGE_PATH_OVERLAP_MIN == 3

    def test_trace_merge_cron(self) -> None:
        """A7 결정 #23 — daily 18 UTC (A6 decay 와 같은 시각)."""
        s = Settings()
        assert s.TRACE_MERGE_CRON == "0 18 * * *"

    def test_leaf_emerging_strict_thresholds(self) -> None:
        """결정 #19 Strict 검증 임계."""
        s = Settings()
        assert s.LEAF_EMERGING_CONFIDENCE_MIN == 0.6
        assert s.LEAF_EMERGING_SUPPORTING_DOCUMENTS_MIN == 3
        assert s.LEAF_EMERGING_LABEL_SIMILARITY_DEDUP == 0.75

    def test_leaf_llm_anchor_retry_cap(self) -> None:
        """결정 #15 — retry cap=1."""
        s = Settings()
        assert s.LEAF_LLM_ANCHOR_RETRY_CAP == 1

    def test_interest_propagation_enabled_default_true(self) -> None:
        """A7 PR-3 머지로 default true (plan #5)."""
        s = Settings()
        assert s.INTEREST_PROPAGATION_ENABLED is True


class TestSplitPathProcessing:
    """A7 결정 #20 — T 단축 + T'=분기점+B (docs SOR 갱신 대상)."""

    def test_split_protocol_has_truncated_path_and_new_path(self) -> None:
        from app.traversal.protocol import SplitPlan

        fields = {f.name for f in SplitPlan.__dataclass_fields__.values()}
        # truncated_path = T 의 새 path / new_path = T' 의 path.
        assert "truncated_path" in fields
        assert "new_path" in fields
        assert "fork_cso_topic_id" in fields
        assert "leaves_to_dispatch" in fields


class TestMergeProtocolFields:
    """trace merge plan dataclass."""

    def test_merge_plan_has_winner_loser(self) -> None:
        from app.traversal.protocol import MergePlan

        fields = {f.name for f in MergePlan.__dataclass_fields__.values()}
        assert "winner_trace_id" in fields
        assert "loser_trace_id" in fields
        assert "leaves_to_reassign" in fields
