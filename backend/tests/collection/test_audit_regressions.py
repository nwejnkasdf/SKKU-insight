"""v13 라운드 A4 회귀 그물 (fail-to-pass guard).

본 테스트들은 다음 표류를 방어한다 (round 1 + round 2 Codex fix):
- (A) v13 sentinel `llm_search` 가 orchestrator 코드에서 참조되지 않으면 실패
- (B) NFR-25 self-summary instruction 이 SYSTEM_PROMPT 에서 사라지면 실패
- (C) source_adapters 디렉토리가 부활하면 (v13 pivot 위반) 실패
- (D) scheduler.JOB_REGISTRATIONS 에 naver_cleanup_job 이 다시 등록되면 (P1-6 무효) 실패
- (E) Document/CollectionJob/DocumentTopic/ClickbaitResult ORM 모델이 누락되면 실패
- (F) collection 모듈에 6 source 어댑터 잔재 코드가 남으면 실패

Round 2 Codex fix 가드:
- (G) C-02: dedup.collapse 가 (to_insert, to_link) 튜플 반환 (단일 list X)
- (H) C-03: orchestrator 가 pg_insert + on_conflict_do_nothing 사용
- (I) S-08: lifespan 이 _SUPPORTED_A4_PROVIDERS 가드 보유
- (J) S-06: JobType 이 contracts.py enum (Literal X)
- (K) N-03: hash_prompt_search 가 SYSTEM_PROMPT_VERSION 포함

모두 정적 검증 (DB 의존 없음).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.collection import dedup, llm_search, orchestrator
from app.collection.llm_search import SYSTEM_PROMPT_TEMPLATE

BACKEND_ROOT = Path(__file__).parent.parent.parent
APP_DIR = BACKEND_ROOT / "app"


class TestV13PivotGuards:
    def test_orchestrator_uses_llm_search_sentinel(self) -> None:
        """orchestrator 가 sentinel name 으로 source_id 를 lookup 해야 한다."""
        src = inspect.getsource(orchestrator)
        assert "llm_search" in src, "orchestrator 가 'llm_search' sentinel 미참조"
        assert orchestrator.LLM_SEARCH_SENTINEL_NAME == "llm_search"

    def test_nfr25_prompt_contains_self_summary(self) -> None:
        """NFR-25 self-summary 키워드 정적 검증 (import-time assertion 의 동적 가드)."""
        assert "본인의 말로" in SYSTEM_PROMPT_TEMPLATE
        assert "1~2문장" in SYSTEM_PROMPT_TEMPLATE

    def test_source_adapters_directory_absent(self) -> None:
        """v13 pivot: 6 어댑터 폐기. source_adapters/ 디렉토리 생성 금지."""
        assert not (APP_DIR / "source_adapters").exists(), (
            "app/source_adapters/ 가 부활했다. v13 pivot 위반 — "
            "decisions.md §10 / decision-backlog C-33 참조."
        )

    def test_naver_cleanup_not_in_scheduler(self) -> None:
        """P1-6 무효: scheduler.JOB_REGISTRATIONS 에 naver_cleanup_job 등록 금지."""
        from app.scheduler import JOB_REGISTRATIONS

        registered_ids = [reg["id"] for reg in JOB_REGISTRATIONS]
        assert "naver_cleanup_job" not in registered_ids, (
            "naver_cleanup_job 이 다시 등록됐다. decision-backlog P1-6 무효 — "
            "v13 라운드 pivot 으로 NaverBS4 폐기."
        )

    def test_clickbait_default_disabled(self) -> None:
        """CLICKBAIT_ENABLED default False 보장 — v13 사용자 결정 매트릭스."""
        from app.config import Settings

        assert Settings.model_fields["CLICKBAIT_ENABLED"].default is False

    def test_dedup_module_has_priority_rules(self) -> None:
        """dedup.collapse 가 4 우선순위 (DOI/canonical/url/title) 호출 함수 보유."""
        src = inspect.getsource(dedup)
        for keyword in ("doi", "canonical_url", "normalized_url", "is_title_duplicate"):
            assert keyword in src, f"dedup module missing keyword: {keyword}"

    def test_llm_search_module_uses_provider_protocol(self) -> None:
        """llm_search.search_for_leaf 가 LLMProvider.search_with_tools 호출."""
        src = inspect.getsource(llm_search)
        assert "search_with_tools" in src


class TestORMModelsPresent:
    @pytest.mark.parametrize(
        "model_name",
        [
            "Document",
            "DocumentTopic",
            "CollectionJob",
            "ClickbaitResult",
        ],
    )
    def test_model_importable(self, model_name: str) -> None:
        import app.db.models as models_pkg

        assert hasattr(models_pkg, model_name), f"db.models missing: {model_name}"


class TestProtocolExtension:
    def test_search_with_tools_in_protocol(self) -> None:
        """LLMProvider Protocol 에 search_with_tools 메서드 시그니처 존재."""
        from app.llm_provider.protocol import LLMProvider

        assert hasattr(LLMProvider, "search_with_tools")

    def test_search_result_dataclass_fields(self) -> None:
        from app.llm_provider.protocol import SearchResult

        fields = {f.name for f in SearchResult.__dataclass_fields__.values()}
        for required in ("title", "url", "abstract_summary", "confidence", "raw"):
            assert required in fields


class TestRound2CodexFixGuards:
    """Codex round 2 fix 회귀 가드 — 누군가 fix 를 되돌리면 즉시 실패."""

    def test_dedup_collapse_returns_tuple(self) -> None:
        """C-02: collapse 가 (to_insert, to_link) 튜플 반환. 단일 list 회귀 차단."""
        from app.collection import dedup
        from app.llm_provider.protocol import SearchResult

        out = dedup.collapse(
            [],
            [SearchResult(title="t", url="https://e.com", abstract_summary="s")],
        )
        assert isinstance(out, tuple) and len(out) == 2

    def test_dedup_key_has_document_id(self) -> None:
        """C-02: DedupKey 에 document_id 필드 존재."""
        from app.collection.dedup import DedupKey

        fields = set(DedupKey.__dataclass_fields__.keys())
        assert "document_id" in fields

    def test_orchestrator_uses_on_conflict(self) -> None:
        """C-03: orchestrator 가 pg_insert + on_conflict_do_nothing 패턴 보유."""
        from app.collection import orchestrator

        src = inspect.getsource(orchestrator)
        assert "pg_insert" in src or "on_conflict_do_nothing" in src

    def test_lifespan_has_supported_provider_guard(self) -> None:
        """S-08: lifespan 이 _SUPPORTED_A4_PROVIDERS 가드 보유."""
        from app import lifespan as lifespan_mod

        assert hasattr(lifespan_mod, "_SUPPORTED_A4_PROVIDERS")
        assert hasattr(lifespan_mod, "_validate_llm_provider")

    def test_job_type_is_enum_in_contracts(self) -> None:
        """S-06: JobType 이 contracts.py enum (inline Literal 회귀 차단)."""
        from app.contracts import JobType

        assert hasattr(JobType, "DAILY_COLLECT")
        assert JobType.DAILY_COLLECT.value == "daily_collect"

    def test_hash_prompt_search_includes_prompt_version(self) -> None:
        """N-03: hash_prompt_search 가 SYSTEM_PROMPT_VERSION 포함 → fixture invalidate."""
        from app.collection import llm_search as llm_search_mod
        from app.llm_provider.mock import hash_prompt_search

        original = llm_search_mod.SYSTEM_PROMPT_VERSION
        h1 = hash_prompt_search({"a": 1}, "leaf", 5)
        try:
            llm_search_mod.SYSTEM_PROMPT_VERSION = original + "_bumped"
            h2 = hash_prompt_search({"a": 1}, "leaf", 5)
        finally:
            llm_search_mod.SYSTEM_PROMPT_VERSION = original
        assert h1 != h2, "prompt version 변경이 fixture hash 에 반영 안 됨"

    def test_run_now_accepts_job_id_arg(self) -> None:
        """S-01: collection_job 시그니처에 job_id_str 파라미터 존재."""
        import inspect as _i

        from app.worker.jobs.collection import collection_job

        sig = _i.signature(collection_job)
        assert "job_id_str" in sig.parameters

    def test_collection_uses_active_trace_before_broad_fallback(self) -> None:
        """A8 demo bridge: dynamic leaf 부재 시 BroadInterest 해시 전에 active trace 를 쓴다."""
        import inspect as _i

        from app.collection import orchestrator as orchestrator_mod

        src = _i.getsource(orchestrator_mod.resolve_active_leaves)
        trace_idx = src.index("_resolve_trace_leaves")
        broad_idx = src.index("_resolve_fallback_leaves")
        assert trace_idx < broad_idx

    def test_documents_service_uses_coalesce(self) -> None:
        """S-04: documents_service 가 coalesce(published_at, created_at) 사용."""
        from app.topic import documents_service

        src = inspect.getsource(documents_service)
        assert "coalesce" in src.lower(), "S-04 fix 누락"

    def test_collection_me_response_has_next_cursor(self) -> None:
        """S-05: CollectionJobMeResponse 에 next_cursor / has_more 필드 존재."""
        from app.collection.schemas import CollectionJobMeResponse

        fields = CollectionJobMeResponse.model_fields
        assert "next_cursor" in fields
        assert "has_more" in fields


class TestRound3CodexFixGuards:
    """Codex round 3 (R2-* re-audit fix) 회귀 가드."""

    def test_insert_idempotent_returns_tuple_with_is_new(self) -> None:
        """R2-S06: _insert_document_idempotent 가 (UUID|None, bool) 튜플 반환."""
        import inspect as _i

        from app.collection.orchestrator import _insert_document_idempotent

        src = _i.getsource(_insert_document_idempotent)
        assert "is_new" in src or "tuple" in src.lower()
        # 시그니처에 tuple return 명시
        sig = _i.signature(_insert_document_idempotent)
        assert "tuple" in str(sig.return_annotation).lower()

    def test_insert_idempotent_uses_untargeted_on_conflict(self) -> None:
        """R2-C01/C02: untargeted on_conflict_do_nothing() 사용 (partial index infer 회피)."""
        import inspect as _i

        from app.collection import orchestrator

        src = _i.getsource(orchestrator)
        # untargeted (인자 없는) on_conflict_do_nothing() 호출이 _insert_document_idempotent
        # 내부에 있어야 함. 옛 .on_conflict_do_nothing(index_elements=...) 패턴은 partial
        # index infer 실패 가능 → 제거됐어야.
        assert ".on_conflict_do_nothing()" in src

    def test_document_topic_upsert_uses_greatest(self) -> None:
        """R2-S04: DocumentTopic upsert 가 greatest(excluded, current) 정책."""
        import inspect as _i

        from app.collection.orchestrator import _upsert_document_topic

        src = _i.getsource(_upsert_document_topic)
        assert "greatest" in src.lower(), "DocumentTopic confidence DO UPDATE greatest 누락"

    def test_openai_search_wraps_json_decode_error(self) -> None:
        """R2-S03: openai.search_with_tools 가 ValueError (JSON decode) → ProviderError."""
        import inspect as _i

        from app.llm_provider import openai as openai_mod

        src = _i.getsource(openai_mod.OpenAIAPIProvider.search_with_tools)
        # response.json() 이 try 블록 안 + ValueError catch + ProviderError raise
        assert "ValueError" in src
        assert "response body parse error" in src or "json parse error" in src.lower()

    def test_trigger_run_now_cleans_up_on_enqueue_failure(self) -> None:
        """R2-S01: enqueue 실패 시 queued row 를 FAILED 마킹."""
        import inspect as _i

        from app.collection.service import trigger_run_now

        src = _i.getsource(trigger_run_now)
        # try/except + status=FAILED + failure_reason 마킹 + db.commit + raise HTTPException
        assert "enqueue_failed" in src or "CollectionJobStatus.FAILED" in src

    def test_orchestrator_retry_increments_count(self) -> None:
        """R2-S02: existing_job_id 재사용 + 이전 status 가 터미널이면 retry_count++."""
        import inspect as _i

        from app.collection.orchestrator import run_collection_for_user

        src = _i.getsource(run_collection_for_user)
        # retry_count 증가 + finished_at/failure_reason 초기화 패턴
        assert "retry_count" in src
        assert "finished_at = None" in src or "finished_at=None" in src
        assert "failure_reason = None" in src or "failure_reason=None" in src

    def test_cron_path_coalesces_recent_user_collection_job(self) -> None:
        """C-63: cron fan-out skips a user recently queued/running/succeeded."""
        import inspect as _i

        from app.collection.orchestrator import (
            _get_recent_user_collection_job,
            run_collection_for_user,
        )

        src = _i.getsource(run_collection_for_user)
        helper_src = _i.getsource(_get_recent_user_collection_job)
        assert "existing_job_id is None" in src
        assert "_get_recent_user_collection_job" in src
        assert "CollectionJobStatus.SUCCEEDED" in helper_src
        assert "CollectionJobStatus.QUEUED" in helper_src
        assert "CollectionJobStatus.RUNNING" in helper_src

    def test_prompt_hash_includes_body(self) -> None:
        """R2-N01: hash_prompt_search 가 SYSTEM_PROMPT_TEMPLATE 본문 변경 시 자동 invalidate."""
        from app.collection import llm_search as llm_search_mod
        from app.llm_provider.mock import hash_prompt_search

        original_body = llm_search_mod.SYSTEM_PROMPT_TEMPLATE
        h1 = hash_prompt_search({"a": 1}, "leaf", 5)
        try:
            llm_search_mod.SYSTEM_PROMPT_TEMPLATE = original_body + "\n# extra line"
            h2 = hash_prompt_search({"a": 1}, "leaf", 5)
        finally:
            llm_search_mod.SYSTEM_PROMPT_TEMPLATE = original_body
        assert h1 != h2, "prompt 본문 변경이 fixture hash 에 자동 반영 안 됨 (R2-N01)"
