"""OpenAIAPIProvider.search_with_tools — Responses API mock unit test (Codex round 2 C-01).

httpx.MockTransport 로 OpenAI 호출을 차단하고, response body 를 직접 fabric.
검증 항목:
- POST 가 `/v1/responses` 로 가는지
- request body 에 tools=[{type:web_search}] 포함
- request body 에 reasoning={"effort": "high"} 포함 (2026-05-18 fix — 사용자 원래
  결정 코드 반영, gpt-5.5 model 일 때만)
- output[].content[].type=output_text 파싱 → SearchResult list
- 실패 응답 → ProviderError
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from app.llm_provider.openai import OpenAIAPIProvider
from app.llm_provider.protocol import ProviderError


@asynccontextmanager
async def _noop_slot(_uid: object) -> AsyncIterator[None]:
    yield


async def _noop_record(_n: int, _redis: object) -> None:
    return None


async def _ok_budget(_redis: object) -> bool:
    return True


def _success_payload(text: str) -> dict[str, Any]:
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


class TestSearchWithTools:
    @pytest.mark.asyncio
    async def test_posts_to_responses_endpoint_with_web_search_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            payload = _success_payload(
                json.dumps(
                    {
                        "results": [
                            {
                                "title": "QML Survey",
                                "url": "https://arxiv.org/abs/2401.01234",
                                "abstract_summary": "QML 본인 말 요약.",
                                "publisher_domain": "arxiv.org",
                                "publisher_label": "arXiv",
                                "published_at": "2026-04-01T00:00:00Z",
                                "doi": "10.48550/arXiv.2401.01234",
                                "confidence": 0.9,
                            }
                        ]
                    }
                )
            )
            return httpx.Response(200, json=payload)

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

        provider = OpenAIAPIProvider()
        results = await provider.search_with_tools(
            {"mode": "test"}, "Quantum ML", top_n=3, user_id=None
        )

        assert captured["url"].endswith("/v1/responses")
        assert captured["body"]["tools"] == [{"type": "web_search"}]
        # 2026-05-18 reasoning_effort fix — search_with_tools 는 high slot 호출.
        # gpt-5.5 default 라 Responses API nested reasoning.effort 박힘.
        assert captured["body"]["reasoning"] == {"effort": "high"}
        assert captured["body"]["model"] == "gpt-5.5"
        assert len(results) == 1
        assert results[0].publisher_domain == "arxiv.org"
        assert results[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_http_error_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal"})

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

        provider = OpenAIAPIProvider()
        with pytest.raises(ProviderError, match="openai search http error"):
            await provider.search_with_tools(
                {"mode": "test"}, "Quantum ML", top_n=3
            )

    @pytest.mark.asyncio
    async def test_malformed_json_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_payload("not a json {{{"))

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

        provider = OpenAIAPIProvider()
        with pytest.raises(ProviderError, match="json parse error"):
            await provider.search_with_tools(
                {"mode": "test"}, "Quantum ML", top_n=3
            )
