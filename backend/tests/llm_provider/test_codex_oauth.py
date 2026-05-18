"""CodexOAuthProvider — `codex exec` subprocess wrap 단위 테스트 (2026-05-18 본문).

`asyncio.create_subprocess_exec` 를 monkeypatch 해서 codex CLI 호출을 차단하고,
fake stdout JSONL 을 직접 fabric. 실제 codex binary 없이도 검증 가능.

검증 항목:
1. argv 조립 — `codex exec --json --ephemeral --skip-git-repo-check --sandbox=...
   --cd=... -c model=... -c model_reasoning_effort=... [--output-schema=...] [- ]`
2. high slot → reasoning_effort=high / medium slot → reasoning_effort=medium
3. response_format=text vs json (json 시 --output-schema 임시 파일 + parsed_json)
4. search_with_tools — `--search` global flag (live mode) 또는 미부재 (cached)
5. JSONL parser — agent_message text 추출 + usage 누적
6. error 처리 — binary not found / exit non-zero / no agent_message / timeout
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.llm_provider import codex_oauth as codex_mod
from app.llm_provider.codex_oauth import CodexOAuthProvider
from app.llm_provider.protocol import ChatMessage, ProviderError


@asynccontextmanager
async def _noop_slot(_uid: object) -> AsyncIterator[None]:
    yield


async def _noop_record(_n: int, _redis: object) -> None:
    return None


async def _ok_budget(_redis: object) -> bool:
    return True


class _FakeProcess:
    """asyncio subprocess 모방 — stdout/stderr/returncode + communicate()."""

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        timeout: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._timeout = timeout
        self._killed = False

    async def communicate(self, _stdin: bytes | None = None) -> tuple[bytes, bytes]:
        if self._timeout:
            # 본 process 가 영원히 응답 안 함 — asyncio.wait_for 가 TimeoutError raise.
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self._killed = True

    async def wait(self) -> int:
        return self.returncode


def _patch_codex_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake_proc: _FakeProcess,
    capture: dict[str, Any],
) -> None:
    """codex_oauth 모듈의 외부 의존 (subprocess, redis, semaphore) monkeypatch."""

    async def _fake_create(*argv: str, **_kw: Any) -> _FakeProcess:
        capture["argv"] = list(argv)
        return fake_proc

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _fake_create
    )
    monkeypatch.setattr(codex_mod, "acquire_slot", _noop_slot)
    monkeypatch.setattr(codex_mod, "check_token_budget", _ok_budget)
    monkeypatch.setattr(codex_mod, "record_token_usage", _noop_record)


def _jsonl(events: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode("utf-8")


def _complete_jsonl(
    text: str,
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
    reasoning_output_tokens: int = 30,
) -> bytes:
    return _jsonl(
        [
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "i0", "type": "agent_message", "text": text},
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_input_tokens": 20,
                    "reasoning_output_tokens": reasoning_output_tokens,
                },
            },
        ]
    )


class TestComplete:
    @pytest.mark.asyncio
    async def test_high_slot_argv_includes_reasoning_effort_high(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture: dict[str, Any] = {}
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(stdout=_complete_jsonl("hello")),
            capture=capture,
        )

        provider = CodexOAuthProvider()
        resp = await provider.complete(
            [ChatMessage(role="user", content="ping")],
            model_slot="high",
            user_id=None,
        )
        argv = capture["argv"]
        assert argv[0] == "codex"
        # global flag --search 부재 (complete 는 검색 X).
        assert "--search" not in argv
        assert "exec" in argv
        assert "--json" in argv
        assert "--ephemeral" in argv
        assert "--skip-git-repo-check" in argv
        # -c key=value 페어 검증.
        assert "-c" in argv
        assert "model=gpt-5.5" in argv
        assert "model_reasoning_effort=high" in argv
        # 2026-05-18 사용자 결정 — 모든 codex 호출에 service_tier=fast.
        assert "service_tier=fast" in argv
        # 2026-05-18 사용자 결정 — codex 자체 personality / .rules 무시.
        # backend prompt 가 SOR (NFR-04 / FR-44 등 자체 제어).
        assert "--ignore-user-config" in argv
        assert "--ignore-rules" in argv
        # response_format=text → --output-schema 부재.
        assert "--output-schema" not in argv
        # 마지막 인자 = `-` (stdin sentinel).
        assert argv[-1] == "-"
        assert resp.text == "hello"
        assert resp.prompt_tokens == 100
        # output_tokens + reasoning_output_tokens = 50 + 30
        assert resp.completion_tokens == 80
        assert resp.meta["reasoning_effort"] == "high"
        assert resp.meta["provider"] == "codex_oauth"

    @pytest.mark.asyncio
    async def test_medium_slot_argv_includes_reasoning_effort_medium(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture: dict[str, Any] = {}
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(stdout=_complete_jsonl("ok")),
            capture=capture,
        )

        provider = CodexOAuthProvider()
        await provider.complete(
            [ChatMessage(role="user", content="ping")],
            model_slot="medium",
            user_id=None,
        )
        assert "model_reasoning_effort=medium" in capture["argv"]
        # service_tier 는 slot 무관 — 항상 박힘 (default fast).
        assert "service_tier=fast" in capture["argv"]

    @pytest.mark.asyncio
    async def test_service_tier_env_override_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CODEX_SERVICE_TIER env 토글 시 argv 에 그대로 반영."""
        capture: dict[str, Any] = {}
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(stdout=_complete_jsonl("ok")),
            capture=capture,
        )
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("CODEX_SERVICE_TIER", "priority")
        try:
            provider = CodexOAuthProvider()
            await provider.complete(
                [ChatMessage(role="user", content="ping")],
                model_slot="high",
                user_id=None,
            )
            assert "service_tier=priority" in capture["argv"]
            assert "service_tier=fast" not in capture["argv"]
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_response_format_json_adds_output_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture: dict[str, Any] = {}
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(
                stdout=_complete_jsonl(json.dumps({"answer": 42}))
            ),
            capture=capture,
        )

        provider = CodexOAuthProvider()
        resp = await provider.complete(
            [ChatMessage(role="user", content="ping")],
            model_slot="high",
            response_format="json",
            user_id=None,
        )
        argv = capture["argv"]
        # --output-schema <tmp.json> 페어 존재.
        assert "--output-schema" in argv
        schema_idx = argv.index("--output-schema")
        schema_path = argv[schema_idx + 1]
        assert schema_path.endswith(".json")
        assert resp.parsed_json == {"answer": 42}

    @pytest.mark.asyncio
    async def test_subprocess_nonzero_exit_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture: dict[str, Any] = {}
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(
                stdout=b"",
                stderr=b"Auth(TokenRefreshFailed)",
                returncode=1,
            ),
            capture=capture,
        )

        provider = CodexOAuthProvider()
        with pytest.raises(ProviderError, match="codex_exec_failed"):
            await provider.complete(
                [ChatMessage(role="user", content="ping")],
                model_slot="high",
                user_id=None,
            )

    @pytest.mark.asyncio
    async def test_no_agent_message_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture: dict[str, Any] = {}
        # agent_message 없는 event stream — turn.completed 만.
        stdout = _jsonl(
            [
                {"type": "thread.started", "thread_id": "t1"},
                {"type": "turn.started"},
                {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}},
            ]
        )
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(stdout=stdout),
            capture=capture,
        )

        provider = CodexOAuthProvider()
        with pytest.raises(ProviderError, match="no_agent_message"):
            await provider.complete(
                [ChatMessage(role="user", content="ping")],
                model_slot="high",
                user_id=None,
            )

    @pytest.mark.asyncio
    async def test_binary_not_found_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_create(*_argv: str, **_kw: Any) -> _FakeProcess:
            raise FileNotFoundError("codex binary missing")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
        monkeypatch.setattr(codex_mod, "acquire_slot", _noop_slot)
        monkeypatch.setattr(codex_mod, "check_token_budget", _ok_budget)
        monkeypatch.setattr(codex_mod, "record_token_usage", _noop_record)

        provider = CodexOAuthProvider()
        with pytest.raises(ProviderError, match="codex_cli_not_found"):
            await provider.complete(
                [ChatMessage(role="user", content="ping")],
                model_slot="high",
                user_id=None,
            )


class TestSearchWithTools:
    @pytest.mark.asyncio
    async def test_cached_mode_omits_search_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture: dict[str, Any] = {}
        # 빈 결과 — fixture 의 의도.
        stdout = _complete_jsonl(json.dumps({"results": []}))
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(stdout=stdout),
            capture=capture,
        )
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("CODEX_WEB_SEARCH_MODE", "cached")
        try:
            provider = CodexOAuthProvider()
            results = await provider.search_with_tools(
                {"mode": "test"}, "Quantum ML", top_n=3, user_id=None
            )
        finally:
            get_settings.cache_clear()
        # cached → --search 부재.
        assert "--search" not in capture["argv"]
        # search_with_tools 도 --output-schema 강제.
        assert "--output-schema" in capture["argv"]
        # high slot 호출.
        assert "model_reasoning_effort=high" in capture["argv"]
        assert results == []

    @pytest.mark.asyncio
    async def test_live_mode_adds_search_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture: dict[str, Any] = {}
        item = {
            "title": "QML Survey",
            "url": "https://arxiv.org/abs/2401.01234",
            "abstract_summary": "본인 말 요약.",
            "publisher_domain": "arxiv.org",
            "publisher_label": "arXiv",
            "published_at": "2026-04-01T00:00:00Z",
            "doi": "10.48550/arXiv.2401.01234",
            "canonical_url": None,
            "confidence": 0.9,
            "raw": {},
        }
        stdout = _complete_jsonl(json.dumps({"results": [item]}))
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(stdout=stdout),
            capture=capture,
        )
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("CODEX_WEB_SEARCH_MODE", "live")
        try:
            provider = CodexOAuthProvider()
            results = await provider.search_with_tools(
                {"mode": "test"}, "Quantum ML", top_n=3, user_id=None
            )
        finally:
            get_settings.cache_clear()
        # live → --search 가 global flag (argv[1] 자리).
        argv = capture["argv"]
        assert argv[0] == "codex"
        assert argv[1] == "--search"
        assert "exec" in argv
        assert len(results) == 1
        assert results[0].title == "QML Survey"
        assert results[0].publisher_domain == "arxiv.org"
        assert results[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_search_response_not_json_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture: dict[str, Any] = {}
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(stdout=_complete_jsonl("not json {{{")),
            capture=capture,
        )

        provider = CodexOAuthProvider()
        with pytest.raises(ProviderError, match="codex_search_json_parse_error"):
            await provider.search_with_tools(
                {"mode": "test"}, "Quantum ML", top_n=3, user_id=None
            )

    @pytest.mark.asyncio
    async def test_search_response_results_wrong_type_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """results 가 list 아닌 type (str/dict) 일 때 ProviderError.

        results key 자체 부재는 빈 list 와 동치 (LLM 이 결과 0건) — raise X.
        """
        capture: dict[str, Any] = {}
        _patch_codex_runtime(
            monkeypatch,
            fake_proc=_FakeProcess(
                stdout=_complete_jsonl(json.dumps({"results": "not a list"}))
            ),
            capture=capture,
        )

        provider = CodexOAuthProvider()
        with pytest.raises(
            ProviderError, match="codex_search_response_missing_results_list"
        ):
            await provider.search_with_tools(
                {"mode": "test"}, "Quantum ML", top_n=3, user_id=None
            )


class TestJsonlParser:
    """`_parse_jsonl_events` 단위 — non-JSON line skip / multi agent_message join."""

    def test_skip_non_json_lines(self) -> None:
        stdout = (
            b'{"type":"turn.started"}\n'
            b'progress: indexing...\n'  # non-JSON
            b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n'
            b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}\n'
        )
        text, usage = codex_mod._parse_jsonl_events(stdout)
        assert text == "ok"
        assert usage["input_tokens"] == 1
        assert usage["output_tokens"] == 2

    def test_multi_agent_message_concatenated(self) -> None:
        stdout = (
            b'{"type":"item.completed","item":{"type":"agent_message","text":"part1 "}}\n'
            b'{"type":"item.completed","item":{"type":"agent_message","text":"part2"}}\n'
            b'{"type":"turn.completed","usage":{}}\n'
        )
        text, _ = codex_mod._parse_jsonl_events(stdout)
        assert text == "part1 part2"

    def test_non_agent_message_items_ignored(self) -> None:
        """web_search_call 같은 다른 item type 은 final_text 에 포함되지 않음."""
        stdout = (
            b'{"type":"item.completed","item":{"type":"web_search_call","query":"..."}}\n'
            b'{"type":"item.completed","item":{"type":"agent_message","text":"final"}}\n'
            b'{"type":"turn.completed","usage":{}}\n'
        )
        text, _ = codex_mod._parse_jsonl_events(stdout)
        assert text == "final"

    def test_usage_mapping_includes_reasoning_tokens(self) -> None:
        """`reasoning_output_tokens` 도 completion 에 누적."""
        usage = {
            "input_tokens": 200,
            "output_tokens": 100,
            "reasoning_output_tokens": 50,
            "cached_input_tokens": 30,
        }
        prompt, completion = codex_mod._map_usage_to_tokens(usage)
        assert prompt == 200
        assert completion == 150
