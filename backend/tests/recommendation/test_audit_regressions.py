"""C-58 demo backfill 폐기 + cleanup 회귀 가드 (정적 source inspection).

사용자 의도: "실제 수거하면 목업 다 없애" — _create_demo_backfill_candidates 가 다시
sentinel pseudo Document 를 INSERT 하지 않도록 가드 + build_dashboard 가 normal 진입 시
옛 pseudo Recommendation 을 cleanup 하는지 가드.
"""
from __future__ import annotations

import inspect

from app.recommendation import engine


def _source(obj: object) -> str:
    return inspect.getsource(obj)


class TestC58DemoBackfillPurge:
    """_create_demo_backfill_candidates 가 빈 list 만 반환."""

    def test_demo_backfill_returns_empty_only(self) -> None:
        src = _source(engine._create_demo_backfill_candidates)
        # 본문에 Document INSERT 또는 sentinel source select 가 없어야 함.
        assert "pg_insert(Document)" not in src
        assert "COLD_START_PSEUDO_NAME" not in src
        assert "follow-up briefing" not in src
        assert "demo_backfill" not in src
        # return [] 단일 path.
        assert "return []" in src

    def test_demo_backfill_signature_preserved(self) -> None:
        """caller 변경 0 — 시그니처는 그대로 (빈 list 가 자연 흐름)."""
        sig = inspect.signature(engine._create_demo_backfill_candidates)
        params = list(sig.parameters.keys())
        assert params == ["db", "user_id", "exclude_ids", "limit"]


class TestC58CleanupPseudoRecommendations:
    """build_dashboard normal 진입 시 옛 pseudo Recommendation cleanup."""

    def test_cleanup_helper_exists(self) -> None:
        assert hasattr(engine, "_cleanup_pseudo_recommendations")

    def test_cleanup_helper_deletes_only_recommendations(self) -> None:
        """Document 는 보존 — Recommendation row 만 DELETE."""
        src = _source(engine._cleanup_pseudo_recommendations)
        assert "DELETE FROM recommendation" in src
        # Document DELETE 는 안 함 (보존).
        assert "DELETE FROM document" not in src
        # user_id scope.
        assert "user_id = :uid" in src
        # pseudo content_type filter.
        assert "pseudo_cold_start" in src

    def test_build_dashboard_calls_cleanup_after_cold_start_branch(self) -> None:
        """_is_cold_start False 직후 cleanup 호출."""
        src = _source(engine.build_dashboard)
        assert "_cleanup_pseudo_recommendations" in src
        # 호출이 _is_cold_start 분기 이후.
        cold_start_idx = src.find("_is_cold_start")
        cleanup_idx = src.find("_cleanup_pseudo_recommendations")
        assert cold_start_idx >= 0 and cleanup_idx > cold_start_idx
