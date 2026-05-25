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


class TestC57LeafDispatchLLM:
    """C-57 retract/split LLM dispatch production caller 회귀 가드."""

    def test_evaluate_retract_calls_llm_helper(self) -> None:
        from app.traversal import default

        src = _source(default.DefaultTraversalEngine.evaluate_retract)
        assert "_llm_retract_decisions" in src
        # stub fallback 직접 인라인은 제거 — helper 안에서 처리.
        assert "1차 시연: 모두 new_path 의 새 말단 노드로 remap fallback" not in src

    def test_evaluate_split_calls_llm_helper(self) -> None:
        from app.traversal import default

        src = _source(default.DefaultTraversalEngine.evaluate_split)
        assert "_llm_split_decisions" in src
        assert "1차 시연 stub: 모두 source 유지" not in src

    def test_llm_retract_decisions_fallback_to_stub(self) -> None:
        from app.traversal import default

        src = _source(default.DefaultTraversalEngine._llm_retract_decisions)
        # ProviderError / FixtureNotFound 시 None 반환 → stub fallback.
        assert "_stub" in src
        assert "call_retract_reposition" in src

    def test_llm_split_decisions_fallback_to_stub(self) -> None:
        from app.traversal import default

        src = _source(default.DefaultTraversalEngine._llm_split_decisions)
        assert "_stub" in src
        assert "call_split_dispatch" in src


class TestC65TraceSystemFixes:
    """C-65 (2026-05-26) trace 시스템 D1/D2 코드 fix 회귀 가드.

    D1 = score_tail 갱신 caller 부재 (Reincarnation 무력화 + core_softmax 균등 분포).
    D2 = stale → active reactivation caller 부재 (§3.2 명세 vs 코드 drift).
    fix 후 회귀 차단 — 정적 source inspection (DB fixture 불요).
    """

    def test_find_active_trace_matching_includes_stale(self) -> None:
        """D2: find_active_traces_matching 가 ACTIVE + STALE 양쪽 검색.

        (C-68 갱신, 2026-05-26) 함수 이름이 `find_active_traces_matching` (multi 반환)
        으로 rename. 옛 `find_active_trace_matching` 은 backward-compat alias.
        """
        from app.traversal import queries

        src = _source(queries.find_active_traces_matching)
        # status.in_([ACTIVE, STALE]) 패턴 — `STALE.value` 가 본문에 등장.
        assert "STALE" in src
        # 정렬에서 active 우선 분기 — case 표현식.
        assert "case" in src or "CASE" in src

    def test_ingest_event_reactivated_branch(self) -> None:
        """D2: ingest_event 가 STALE 매칭 시 status='active' + reactivated 반환."""
        from app.traversal import default

        src = _source(default.DefaultTraversalEngine.ingest_event)
        assert "reactivated" in src
        # status=STALE 검사 후 ACTIVE 전이.
        assert "TraversalStatus.STALE" in src
        assert "TraversalStatus.ACTIVE" in src

    def test_traversal_delta_includes_reactivated(self) -> None:
        """D2: TraversalDelta Literal 에 'reactivated' 추가."""
        from app.traversal.protocol import TraversalDelta
        # typing.Literal[...].__args__ 로 enum 값 추출.
        args = getattr(TraversalDelta, "__args__", ())
        assert "reactivated" in args
        # 기존 값 보존 (backward-compat).
        assert "promoted" in args
        assert "new_trace" in args

    def test_daily_trace_update_counts_reactivated(self) -> None:
        """D2: daily_trace_update 의 update_traces_from_recent_events 가 reactivated count."""
        from app.traversal import daily_trace_update

        src = _source(daily_trace_update.update_traces_from_recent_events)
        # delta "reactivated" 도 updated count.
        assert '"reactivated"' in src or "'reactivated'" in src
        # 단, any_behavioral_signal 트리거 X (옛 boost 정리 무관).
        # 즉 promoted/new_trace 만 boost cleanup 트리거.

    def test_sync_score_tail_helpers_exist(self) -> None:
        """D1: operations.sync_score_tail_for_user + sync_score_tail_for_trace 존재."""
        from app.traversal import operations

        assert hasattr(operations, "sync_score_tail_for_user")
        assert hasattr(operations, "sync_score_tail_for_trace")
        # 단일 atomic SQL — text() 사용.
        src_user = _source(operations.sync_score_tail_for_user)
        assert "UPDATE user_cso_traversal" in src_user
        assert "long_score" in src_user
        # IS DISTINCT FROM 가드 — no-op 트랜잭션 회피.
        assert "IS DISTINCT FROM" in src_user

    def test_operations_imports_sqlalchemy_text(self) -> None:
        """(C-65 후속 fix, 2026-05-26) sync_score_tail_for_user/for_trace 가 text() 사용.

        operations.py module 에 `from sqlalchemy import text` import 부재 시 caller
        (`execute_extend`/`retract`/`split`, `create_new_trace`, `_bootstrap_boost_traces`,
        `_promote_fusion`) 모두 NameError 발생 — bootstrap_interest_state savepoint
        rollback + boost trace 0 + dashboard fallback_trend 도배 결함.
        """
        from app.traversal import operations

        # text 가 module level 에 binding 됨.
        assert hasattr(operations, "text"), (
            "sqlalchemy.text 가 operations.py 에 import 안 됨 — "
            "sync_score_tail_for_user/for_trace 호출 시 NameError."
        )

    def test_ingest_event_atomic_calls_sync_score_tail(self) -> None:
        """D1: ingest_event_atomic step 7.5 가 sync_score_tail_for_user 호출 — lock 보유 안."""
        from app.interest import service

        src = _source(service.ingest_event_atomic)
        assert "sync_score_tail_for_user" in src
        # mark_stale_if_idle 직전에 호출 — 직전 베이지안 사후 결과 반영.
        # 두 호출이 같은 lock 보유 안 (text 위치 검증은 정적 inspection 어려움 — 호출 존재만).
        assert "mark_stale_if_idle" in src

    def test_execute_archive_calls_sync_score_tail_freeze(self) -> None:
        """D1: execute_archive 가 status UPDATE 직전 sync_score_tail_for_trace 호출 (freeze)."""
        from app.traversal import operations

        src = _source(operations.execute_archive)
        assert "sync_score_tail_for_trace" in src

    def test_execute_merge_loser_archive_freezes_score_tail(self) -> None:
        """D1: execute_merge 의 loser archive 직전 sync_score_tail_for_trace 호출."""
        from app.traversal import operations

        src = _source(operations.execute_merge)
        assert "sync_score_tail_for_trace" in src
        # loser_trace_id 가 freeze 대상.
        assert "plan.loser_trace_id" in src


class TestC66PathChangeSyncScoreTail:
    """C-66 (2026-05-26) path 변경 operation 의 score_tail sync 회귀 가드.

    후속 #1+#2 — C-65 ingest hook + archive freeze 외에 path 변경 operation 6곳
    (extend / retract / split source+new / create_new_trace / _bootstrap_boost_traces /
    _promote_fusion) 모두 sync_score_tail_for_trace 또는 sync_score_tail_for_user 호출.

    _promote_reincarnation 은 archive 시점 freeze 값 보존 (B1, Serendipity 정합) — sync 호출 부재 검증.
    """

    def test_execute_extend_calls_sync_score_tail(self) -> None:
        """execute_extend 가 path append 직후 sync_score_tail_for_trace 호출."""
        from app.traversal import operations

        src = _source(operations.execute_extend)
        assert "sync_score_tail_for_trace" in src

    def test_execute_retract_calls_sync_score_tail_after_path_pop(self) -> None:
        """execute_retract 가 path pop UPDATE 직후 sync_score_tail_for_trace 호출 — leaf 변경 전."""
        from app.traversal import operations

        src = _source(operations.execute_retract)
        # path UPDATE 와 leaf 변경 사이 sync 호출 — plan.trace_id 대상.
        assert "sync_score_tail_for_trace" in src
        # 호출 위치가 path UPDATE 직후 (leaf decisions 적용 직전).
        # 정적 검증: leaf decisions 보다 sync 가 먼저 등장.
        sync_idx = src.find("sync_score_tail_for_trace")
        leaf_idx = src.find("leaf_remap_decisions")
        assert sync_idx >= 0 and leaf_idx >= 0
        assert sync_idx < leaf_idx, "sync 호출이 leaf decisions 적용 직전이어야 함"

    def test_execute_split_calls_sync_twice(self) -> None:
        """execute_split 가 source UPDATE 직후 + new T' INSERT 직후 양쪽 sync 호출."""
        from app.traversal import operations

        src = _source(operations.execute_split)
        # 2번 등장 — source + new (또는 그 이상 — 최소 2).
        assert src.count("sync_score_tail_for_trace") >= 2
        # source_trace_id + new T' id 양쪽 sync 호출.
        assert "plan.source_trace_id" in src

    def test_create_new_trace_calls_sync(self) -> None:
        """default.create_new_trace 가 INSERT 직후 sync_score_tail_for_trace 호출."""
        from app.traversal import default

        src = _source(default.DefaultTraversalEngine.create_new_trace)
        assert "sync_score_tail_for_trace" in src

    def test_bootstrap_boost_traces_calls_sync_for_user(self) -> None:
        """_bootstrap_boost_traces 가 batch INSERT 후 sync_score_tail_for_user 호출."""
        from app.interest import service

        src = _source(service._bootstrap_boost_traces)
        assert "sync_score_tail_for_user" in src
        # flush 후 sync — db.add() 만으로는 SELECT 안 보이므로.
        assert "db.flush" in src

    def test_promote_fusion_calls_sync(self) -> None:
        """_promote_fusion 가 새 trace INSERT 후 sync_score_tail_for_trace 호출."""
        from app.worker.jobs import weekly_promotion

        src = _source(weekly_promotion._promote_fusion)
        assert "sync_score_tail_for_trace" in src

    def test_promote_reincarnation_no_sync_call_b1_freeze(self) -> None:
        """B1 — _promote_reincarnation 은 archive 시점 freeze 값 보존, sync 호출 부재.

        Serendipity 'taste reincarnation' 본질 — archive 시점 강도 그대로 active 복귀.
        사용자 활동 시 ingest sync hook 이 자연 갱신 (별도 caller 불요).
        """
        from app.worker.jobs import weekly_promotion

        src = _source(weekly_promotion._promote_reincarnation)
        assert "sync_score_tail" not in src


class TestC67MultiCsoTraceOverproduction:
    """C-67 (2026-05-26) multi-cso 매핑 trace 과잉 생성 fix 회귀 가드.

    후속 #3 — 직전 update_traces_from_recent_events 가 cso 별 ingest_event 호출 →
    1 doc click 의 multi-cso 매핑 (C-59 cluster_root + related_csos) 이 trace 4개 생성.
    fix = qualifying_csos list 통째 ingest_event 1번 호출 → 첫 매칭/첫 cso 새 trace 1개.
    """

    def test_update_traces_uses_single_ingest_call(self) -> None:
        """update_traces_from_recent_events 가 qualifying_csos list 통째 ingest_event
        1번 호출. cso 별 loop 호출 부재 (`for cso_id in qualifying_csos: ingest_event(...)`
        패턴 제거).
        """
        from app.traversal import daily_trace_update

        src = _source(daily_trace_update.update_traces_from_recent_events)
        # ingest_event 호출은 1번 — list 통째 전달.
        assert src.count("await engine.ingest_event") == 1
        # qualifying_csos list 전달 (loop iter 변수 X).
        assert "engine.ingest_event(\n                user_id, current_active_day, qualifying_csos\n            )" in src or \
            "engine.ingest_event(user_id, current_active_day, qualifying_csos)" in src or \
            "qualifying_csos" in src and "for cso_id in qualifying_csos:" not in src

    def test_qualifying_csos_sorted_by_count_desc(self) -> None:
        """qualifying_csos 정렬 — count DESC + cso_id ASC (deterministic).

        ingest_event 가 list 첫 매칭/첫 cso 우선 처리하므로 가장 빈도 높은 영역이
        먼저. tie 시 cso_id ASC (deterministic — 같은 날 동일 사용자 결과 재현).
        """
        from app.traversal import daily_trace_update

        src = _source(daily_trace_update.update_traces_from_recent_events)
        # sorted 호출 + count DESC key.
        assert "sorted(" in src
        assert "-cso_counter[c]" in src or "cso_counter[c]" in src and "-" in src

    def test_update_counts_at_most_one_trace_change(self) -> None:
        """단일 ingest_event 호출 결과는 trace 변동 최대 1건 (delta 1개) — updated=1 또는 0."""
        from app.traversal import daily_trace_update

        src = _source(daily_trace_update.update_traces_from_recent_events)
        # `updated += 1` 패턴 부재 — `updated = 1` 단일 set.
        assert "updated += 1" not in src
        assert "updated = 1" in src


class TestC68MultiTraceMatching:
    """C-68 (2026-05-26) find_active_traces_matching multi 반환 fix 회귀 가드.

    후속 #4 — 직전 `find_active_trace_matching` LIMIT 1 → 같은 cso 가 multi-trace
    path 매핑된 경우 (split 후 분기점, fusion bridge_cso 가 active path 위 노드 등)
    한쪽만 갱신 결함. fix = `find_active_traces_matching` (multi 반환) + `ingest_event`
    가 list iterate 모두 갱신.
    """

    def test_find_active_traces_matching_returns_list(self) -> None:
        """find_active_traces_matching 가 list[UserCSOTraversal] 반환 (LIMIT 부재)."""
        from app.traversal import queries

        src = _source(queries.find_active_traces_matching)
        # LIMIT 1 부재 (multi 반환).
        assert ".limit(1)" not in src
        # list 반환.
        assert "list((await db.execute(stmt)).scalars().all())" in src

    def test_find_active_trace_matching_alias_uses_multi(self) -> None:
        """backward-compat alias `find_active_trace_matching` 가 multi 함수의 첫 결과 반환.

        옛 caller 호환 — None 또는 list[0]. 신규 caller 는 multi 함수 직접 사용.
        """
        from app.traversal import queries

        src = _source(queries.find_active_trace_matching)
        # multi 함수 호출.
        assert "find_active_traces_matching" in src
        # list[0] 또는 None 반환.
        assert "matches[0]" in src or "matches and matches[0]" in src

    def test_ingest_event_iterates_all_matches(self) -> None:
        """ingest_event 가 find_active_traces_matching 결과 list iterate 모두 갱신.

        multi-trace 매칭 케이스 (split 후 분기점) 모두 last_activity / status / origin 갱신.
        """
        from app.traversal import default

        src = _source(default.DefaultTraversalEngine.ingest_event)
        # multi 함수 호출.
        assert "find_active_traces_matching" in src
        # matches list iterate.
        assert "for matched in matches:" in src
        # promoted_any / reactivated_any — 1개라도 boost/stale 였으면 분기.
        assert "promoted_any" in src
        assert "reactivated_any" in src
