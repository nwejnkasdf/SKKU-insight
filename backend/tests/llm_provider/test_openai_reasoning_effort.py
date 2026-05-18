"""OpenAIAPIProvider.complete — reasoning_effort payload 회귀 가드 (2026-05-18 fix).

사용자 원래 결정 (high slot → reasoning_effort=high / medium slot → medium) 이
코드에 반영됐는지 검증. 직전까지 openai.py 가 reasoning_effort 를 payload 에 박지
않아서, high slot 호출도 OpenAI default (medium) 로 동작하던 결함의 회귀 가드.

검증 케이스:
1. gpt-5.5 + model_slot="high" → payload["reasoning_effort"] == "high"
2. gpt-5.5 + model_slot="medium" → payload["reasoning_effort"] == "medium"
3. gpt-5.5 (high) + Settings 토글 → 토글된 값 그대로 반영
4. 비 reasoning 모델 (gpt-4o) → payload 에 reasoning_effort 키 부재 (400 회피)
5. search_with_tools — gpt-5.5 → Responses API nested reasoning={"effort": "high"}
6. search_with_tools — gpt-4o (비 reasoning) → reasoning 키 부재
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from app.llm_provider.openai import OpenAIAPIProvider
from app.llm_provider.protocol import ChatMessage


@asynccontextmanager
async def _noop_slot(_uid: object) -> AsyncIterator[None]:
    yield


async def _noop_record(_n: int, _redis: object) -> None:
    return None


async def _ok_budget(_redis: object) -> bool:
    return True


def _chat_success(text: str = "ok") -> dict[str, Any]:
    return {
        "id": "chatcmpl_test",
        "model": "gpt-5.5",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _responses_success(text: str) -> dict[str, Any]:
    return {
        "id": "resp_test",
        "model": "gpt-5.5",
        "usage": {"input_tokens": 100, "output_tokens": 200},
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def _patch_httpx_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Any
) -> None:
    """httpx.AsyncClient 의 transport 를 MockTransport 로 교체."""
    from app.llm_provider import _concurrency as conc_mod
    from app.llm_provider import openai as openai_mod

    monkeypatch.setattr(openai_mod, "acquire_slot", _noop_slot)
    monkeypatch.setattr(openai_mod, "check_token_budget", _ok_budget)
    monkeypatch.setattr(openai_mod, "record_token_usage", _noop_record)
    monkeypatch.setattr(conc_mod, "acquire_slot", _noop_slot)

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def _patched_init(self: httpx.AsyncClient, *a: Any, **kw: Any) -> None:
        kw["transport"] = transport
        orig_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


class TestChatCompletionsReasoningEffort:
    """Chat Completions API — top-level `reasoning_effort` key."""

    @pytest.mark.asyncio
    async def test_gpt5_high_slot_sets_reasoning_effort_high(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_success())

        _patch_httpx_transport(monkeypatch, handler)

        provider = OpenAIAPIProvider()
        await provider.complete(
            [ChatMessage(role="user", content="ping")],
            model_slot="high",
            user_id=None,
        )
        assert captured["body"]["reasoning_effort"] == "high"
        # GPT-5 series — temperature 미전송 (Unsupported value 차단).
        assert "temperature" not in captured["body"]

    @pytest.mark.asyncio
    async def test_gpt5_medium_slot_sets_reasoning_effort_medium(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_success())

        _patch_httpx_transport(monkeypatch, handler)

        provider = OpenAIAPIProvider()
        await provider.complete(
            [ChatMessage(role="user", content="ping")],
            model_slot="medium",
            user_id=None,
        )
        assert captured["body"]["reasoning_effort"] == "medium"

    @pytest.mark.asyncio
    async def test_settings_override_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """xhigh 등 다른 값으로 토글 시 그대로 전송. (운영자 책임 — 사용자 결정은 high/medium)"""
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_success())

        _patch_httpx_transport(monkeypatch, handler)
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("LLM_REASONING_EFFORT_HIGH", "xhigh")
        monkeypatch.setenv("LLM_REASONING_EFFORT_MEDIUM", "low")
        try:
            provider = OpenAIAPIProvider()
            await provider.complete(
                [ChatMessage(role="user", content="ping")],
                model_slot="high",
                user_id=None,
            )
            assert captured["body"]["reasoning_effort"] == "xhigh"
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_non_gpt5_model_omits_reasoning_effort(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """gpt-4o 등 비 reasoning 모델 토글 시 reasoning_effort 키 부재 — 400 회피."""
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_success())

        _patch_httpx_transport(monkeypatch, handler)
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("LLM_MODEL_HIGH", "gpt-4o")
        monkeypatch.setenv("LLM_MODEL_MEDIUM", "gpt-4o")
        try:
            provider = OpenAIAPIProvider()
            await provider.complete(
                [ChatMessage(role="user", content="ping")],
                model_slot="high",
                user_id=None,
            )
            assert "reasoning_effort" not in captured["body"]
            # 비 GPT-5 모델 — temperature 전송됨.
            assert "temperature" in captured["body"]
        finally:
            get_settings.cache_clear()


class TestResponsesApiReasoning:
    """Responses API — nested `reasoning.effort` key (search_with_tools)."""

    @pytest.mark.asyncio
    async def test_gpt5_search_uses_nested_reasoning_high(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            payload = _responses_success(json.dumps({"results": []}))
            return httpx.Response(200, json=payload)

        _patch_httpx_transport(monkeypatch, handler)

        provider = OpenAIAPIProvider()
        await provider.search_with_tools(
            {"mode": "test"}, "Quantum ML", top_n=3, user_id=None
        )
        assert captured["body"]["reasoning"] == {"effort": "high"}
        assert captured["body"]["tools"] == [{"type": "web_search"}]

    @pytest.mark.asyncio
    async def test_non_gpt5_search_omits_reasoning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            payload = _responses_success(json.dumps({"results": []}))
            return httpx.Response(200, json=payload)

        _patch_httpx_transport(monkeypatch, handler)
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("LLM_MODEL_HIGH", "gpt-4o")
        try:
            provider = OpenAIAPIProvider()
            await provider.search_with_tools(
                {"mode": "test"}, "Quantum ML", top_n=3, user_id=None
            )
            assert "reasoning" not in captured["body"]
        finally:
            get_settings.cache_clear()
