"""A8-v2 본문 anti-pattern 회귀 가드 (정적 source inspection).

A6/A7/A8 fix 누적 lesson — A8-v2 본문이 같은 anti-pattern 을 다시 도입하지 않도록 가드.
P2 백로그: 정적 검사는 변수명 변경 우회 가능 — 완전한 검증은 통합 시연 (docker compose).

검증 대상 (모두 본문 source inspect):
1. cache-before-commit 회피 — worker._run 의 db.commit() 이 redis.delete 보다 앞.
2. Lua atomic CAS release — `_RELEASE_LOCK_LUA` 토큰 비교 + DEL.
3. per-user try/except + commit — for-loop body 안 try/except/finally.
4. UserProfile upsert = `pg_insert.on_conflict_do_update` 단일 SQL.
5. NFR-04 거부 키워드 강화 — reasons.py 의 `_REJECTED_KEYWORDS` 에 A8-v2 추가 키워드.
6. LLM hallucination 가드 — generate_profile_payload 의 bridge_cso_topic_id 매핑 검사.
7. archive filter — fetch_profile_llm_input 이 score_tail_min 적용.
8. discovery fallback chain — engine._build_discovery_pool_raw 의 4단계 fallback 존재.
"""
from __future__ import annotations

import inspect
from typing import Any

from app.config import Settings
from app.contracts import ErrorCode, JobType, RedisKey
from app.db.models import UserProfile
from app.profile import service as profile_service
from app.recommendation import candidates as rec_candidates
from app.recommendation import engine as rec_engine
from app.recommendation import reasons as rec_reasons
from app.traversal import queries as trav_queries
from app.worker.jobs import user_profile as profile_worker


def _source(obj: Any) -> str:
    return inspect.getsource(obj)


class TestCacheBeforeCommit:
    """A4 C-02 / A6 C-02 / A8 §11 #1 — db.commit() → redis.delete 순서."""

    def test_worker_commit_before_cache_delete(self) -> None:
        src = _source(profile_worker._run)
        commit_idx = src.find("await db.commit()")
        # Codex R1 Suggested #1 fix (2026-05-19): cache invalidate 가 finally 의
        # `if committed:` 안. db.commit() 이 본 블록 보다 앞.
        delete_idx = src.find("redis.delete(\n                            RedisKey.recommendation_cache")
        assert commit_idx > 0
        assert delete_idx > 0
        assert commit_idx < delete_idx, (
            "db.commit() 이 redis.delete 보다 앞 (cache-before-commit 회피)"
        )

    def test_cache_invalidate_isolated_in_finally(self) -> None:
        """Codex R1 Suggested #1 fix — cache delete 가 별도 try/except 로 분리."""
        src = _source(profile_worker._run)
        # committed 플래그 + finally 안 if committed.
        assert "committed = False" in src
        assert "committed = True" in src
        assert "if committed:" in src
        # cache invalidate 실패 warning (rollback 처리 안 함).
        assert "cache invalidate failed" in src


class TestLuaAtomicRelease:
    """A7 R2-RG-3 — Lua atomic GET+DEL CAS release."""

    def test_release_lock_lua_constant_defined(self) -> None:
        assert hasattr(profile_worker, "_RELEASE_LOCK_LUA")
        lua = profile_worker._RELEASE_LOCK_LUA
        assert "GET" in lua and "DEL" in lua

    def test_run_uses_lua_release(self) -> None:
        src = _source(profile_worker._run)
        assert "redis.eval" in src
        assert "_RELEASE_LOCK_LUA" in src


class TestPerUserTryExcept:
    """A6 C-03 — per-user try/except + commit, batch IntegrityError 회피."""

    def test_run_has_try_except_finally_in_loop(self) -> None:
        src = _source(profile_worker._run)
        # for-loop body 안 try / except / finally 구조.
        assert "for user in users:" in src
        assert "try:" in src
        assert "except Exception:" in src
        assert "finally:" in src
        # db.rollback() 가 except 블록 안.
        assert "await db.rollback()" in src


class TestUpsertSingleSql:
    """A6 C-01 — read-then-write race 회피. pg_insert.on_conflict_do_update 단일 SQL."""

    def test_upsert_uses_pg_insert_on_conflict(self) -> None:
        src = _source(profile_service.upsert_user_profile)
        assert "pg_insert" in src
        assert "on_conflict_do_update" in src

    def test_upsert_specifies_user_id_pk(self) -> None:
        src = _source(profile_service.upsert_user_profile)
        # PK conflict 지정 — partial unique 분기 불필요.
        assert 'index_elements=["user_id"]' in src


class TestNfr04Keywords:
    """A8 §11 #4 + A8-v2 강화 — score / 버킷 / score_tail 거부."""

    def test_score_tail_keyword_rejected(self) -> None:
        keywords = rec_reasons._REJECTED_KEYWORDS
        assert "score_tail" in keywords
        assert "버킷" in keywords
        assert "신뢰도" in keywords


class TestLlmHallucinationGuard:
    """A8-v2 신규 — LLM 응답의 bridge_cso_topic_id 가 cso_graph 안에 있는지 매핑 검사."""

    def test_generate_payload_filters_invalid_bridge(self) -> None:
        src = _source(profile_service.generate_profile_payload)
        # cso_graph 매핑 검사.
        assert "bridge_cso_topic_id in cso_graph" in src
        # deepening/broadening seeds 도 같은 매핑.
        assert "cso_topic_id in cso_graph" in src


class TestArchiveFilter:
    """A8-v2 — fetch_profile_llm_input 이 score_tail_min 적용."""

    def test_fetch_uses_archive_score_tail_min(self) -> None:
        src = _source(profile_service.fetch_profile_llm_input)
        assert "archive_score_tail_min" in src
        assert "get_archived_traces_with_score" in src

    def test_traversal_query_filters_score_tail(self) -> None:
        src = _source(trav_queries.get_archived_traces_with_score)
        # WHERE score_tail >= :score_tail_min.
        assert "score_tail >= score_tail_min" in src or "score_tail >=" in src


class TestDiscoveryFallbackChain:
    """A8-v2 — engine._build_discovery_pools (sub-slot 별 별도) + per-source fallback.

    Codex R1 Critical #2 fix (2026-05-19): 직전 `_build_discovery_pool_raw` 가 두 source
    를 untagged pool 로 통합 → slot 분배 안 강제. 현재 `_build_discovery_pools` 가
    (fusion_pool, reincarnation_pool) tuple 반환, 각 fallback chain 별도.
    """

    def test_engine_pools_helper_defined(self) -> None:
        assert hasattr(rec_engine, "_build_discovery_pools")
        # Deprecated wrapper 도 보존 (backward-compat).
        assert hasattr(rec_engine, "_build_discovery_pool_raw")

    def test_fusion_subslot_fallback_chain(self) -> None:
        src = _source(rec_engine._build_fusion_subslot)
        assert "fusion_candidates" in src
        assert "broadening_seeds" in src
        assert "query_discovery_trend" in src

    def test_reincarnation_subslot_fallback_chain(self) -> None:
        src = _source(rec_engine._build_reincarnation_subslot)
        assert "get_top_archived_trace" in src
        assert "query_discovery_reincarnation" in src
        assert "deepening_seeds" in src
        assert "query_discovery_trend" in src

    def test_doc_result_based_fallback(self) -> None:
        """Codex R1 Suggested #4 — candidate 존재가 아니라 doc rows 가 있어야 사용."""
        src_fusion = _source(rec_engine._build_fusion_subslot)
        src_reincarnation = _source(rec_engine._build_reincarnation_subslot)
        # `if rows: return rows` 패턴 — empty list 면 fallback 진행.
        assert "if rows:" in src_fusion
        assert "if rows:" in src_reincarnation

    def test_trace_path_exclusion_in_fusion(self) -> None:
        """Codex R1 Suggested #3 — fusion bridge_cso 가 active path 위 노드면 거부."""
        src = _source(rec_engine._resolve_seed_id)
        assert "excluded" in src
        assert "in excluded" in src

    def test_engine_builds_two_pools_separately(self) -> None:
        """Codex R1 Critical #2 — build_dashboard 가 두 pool 분리 ranking + [:1] concat."""
        src = _source(rec_engine.build_dashboard)
        # _build_discovery_pools 호출.
        assert "_build_discovery_pools" in src
        # 두 pool 변수 별도.
        assert "fusion_pool_raw" in src
        assert "reincarnation_pool_raw" in src
        # source 별 1개씩 concat.
        assert "fusion_pool[:1] + reincarnation_pool[:1]" in src


class TestCandidateQueries:
    """A8-v2 — 3 신규 query 시그니처."""

    def test_query_discovery_fusion_exists(self) -> None:
        assert callable(rec_candidates.query_discovery_fusion)

    def test_query_discovery_reincarnation_exists(self) -> None:
        assert callable(rec_candidates.query_discovery_reincarnation)

    def test_query_discovery_trend_exists(self) -> None:
        # 기존 query_discovery 가 _trend 로 rename + alias.
        assert callable(rec_candidates.query_discovery_trend)
        # backward-compat alias.
        assert callable(rec_candidates.query_discovery)


class TestContracts:
    """A8-v2 신규 enum/RedisKey/ErrorCode 등록."""

    def test_job_type_user_profile_registered(self) -> None:
        assert JobType.DAILY_USER_PROFILE_GENERATION.value == (
            "daily_user_profile_generation"
        )

    def test_redis_keys_registered(self) -> None:
        # 핵심 prefix 만 검증.
        from uuid import uuid4

        uid = uuid4()
        assert RedisKey.user_profile_generation_lock(uid).startswith(
            "lock:user_profile_gen:"
        )
        assert RedisKey.user_profile_cache(uid).startswith("user_profile:")

    def test_error_codes_registered(self) -> None:
        assert ErrorCode.PROFILE_LLM_OUTPUT_INVALID.value == "profile.llm_output_invalid"
        assert ErrorCode.PROFILE_BRIDGE_CSO_NOT_FOUND.value == (
            "profile.bridge_cso_not_found"
        )


class TestSettings:
    """A8-v2 신규 env 7건."""

    def test_user_profile_env_defaults(self) -> None:
        # Settings 인스턴스 직접 — env 미설정 시 default 값.
        settings = Settings(JWT_SECRET="x" * 64)
        assert settings.USER_PROFILE_CRON == "0 19 * * *"
        assert settings.USER_PROFILE_ARCHIVE_SCORE_TAIL_MIN == 0.6
        assert settings.USER_PROFILE_GENERATOR_VERSION == "v1"
        assert settings.USER_PROFILE_INPUT_ARCHIVE_MAX == 8
        assert settings.USER_PROFILE_REINCARNATION_GAP_DAYS_MIN == 7
        # Codex R1 Critical #1 fix (2026-05-19): 180 → 360 (2x LLM timeout 마진).
        assert settings.USER_PROFILE_LOCK_TTL_SECONDS == 360
        assert settings.USER_PROFILE_CACHE_TTL_SECONDS == 3600


class TestOrmModel:
    """alembic 0007 ↔ ORM 미러링."""

    def test_user_profile_columns(self) -> None:
        columns = {col.name for col in UserProfile.__table__.columns}
        assert "user_id" in columns
        assert "recent_signals_summary" in columns
        assert "persistent_tendencies_summary" in columns
        assert "likely_dislikes_summary" in columns
        assert "fusion_candidates" in columns
        assert "deepening_seeds" in columns
        assert "broadening_seeds" in columns
        assert "generator_version" in columns
        assert "generated_at" in columns
        assert "updated_at" in columns

    def test_user_profile_pk_is_user_id(self) -> None:
        pk_cols = [col.name for col in UserProfile.__table__.primary_key]
        assert pk_cols == ["user_id"]
