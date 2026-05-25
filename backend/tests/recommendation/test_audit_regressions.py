"""C-58 demo backfill 폐기 + cleanup 회귀 가드 (정적 source inspection).

사용자 의도: "실제 수거하면 목업 다 없애" — _create_demo_backfill_candidates 가 다시
sentinel pseudo Document 를 INSERT 하지 않도록 가드 + build_dashboard 가 normal 진입 시
옛 pseudo Recommendation 을 cleanup 하는지 가드.

C-61 후속 (2026-05-25): 수집 진행 중 dashboard refresh 차단 + DashboardResponse 의
collection_in_progress 필드 정합 회귀 가드.
"""
from __future__ import annotations

import inspect

from app.contracts import ErrorCode
from app.recommendation import engine, service
from app.recommendation.schemas import DashboardResponse


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

    def test_cleanup_invalidates_redis_cache(self) -> None:
        """(C-58 followup) DELETE 발생 시 Redis cache 도 invalidate — stale pseudo
        카드 cache hit 복원 race 차단.
        """
        src = _source(engine._cleanup_pseudo_recommendations)
        assert "redis.delete" in src
        assert "RedisKey.recommendation_cache" in src


class TestC61CollectionInProgressGuard:
    """C-61 후속 — 수집 진행 중 dashboard refresh 차단 + collection_in_progress 필드 정합.

    수집 lock 보유 사용자가 refresh 시 backend 가 409 차단하지 않으면 부분 수집 상태 build →
    임계 미달 → trend fallback. UI lock 외 backend 정합 회귀 가드.
    """

    def test_dashboard_response_has_collection_in_progress_field(self) -> None:
        """schema 필드 누락 시 client 가 폴링 / disable 못 함."""
        assert "collection_in_progress" in DashboardResponse.model_fields

    def test_error_code_for_in_progress_refresh_exists(self) -> None:
        """client messageForError 매핑이 의존하는 코드."""
        assert hasattr(ErrorCode, "RECOMMENDATION_COLLECTION_IN_PROGRESS")
        assert (
            ErrorCode.RECOMMENDATION_COLLECTION_IN_PROGRESS.value
            == "recommendation.collection_in_progress"
        )

    def test_refresh_dashboard_blocks_when_lock_present(self) -> None:
        """service.refresh_dashboard 가 collection_lock 존재 시 409 raise."""
        src = _source(service.refresh_dashboard)
        assert "RedisKey.collection_lock" in src
        assert "HTTP_409_CONFLICT" in src
        assert "RECOMMENDATION_COLLECTION_IN_PROGRESS" in src

    def test_cache_hit_recomputes_collection_in_progress(self) -> None:
        """stale cache hit 응답이 그대로 false 반환 시 UI 가 영원히 unlock 상태.
        _try_load_cache 가 redis.exists 로 응답 직전 재계산해야 함.
        """
        src = _source(service._try_load_cache)
        assert "RedisKey.collection_lock" in src
        assert "collection_in_progress" in src

    def test_engine_populates_collection_in_progress_in_both_paths(self) -> None:
        """build_dashboard normal path + _load_cold_start_dashboard 모두 채워야 함."""
        normal_src = _source(engine.build_dashboard)
        cold_src = _source(engine._load_cold_start_dashboard)
        assert "_is_collection_in_progress" in normal_src
        assert "_is_collection_in_progress" in cold_src
        assert "collection_in_progress" in normal_src
        assert "collection_in_progress" in cold_src

    def test_is_collection_in_progress_uses_redis_exists(self) -> None:
        """helper 가 redis.exists(collection_lock) 패턴이어야 stale X."""
        src = _source(engine._is_collection_in_progress)
        assert "redis.exists" in src
        assert "RedisKey.collection_lock" in src
