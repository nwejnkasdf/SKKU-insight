"""llm_search SYSTEM_PROMPT 정적 검증 + MockProvider fixture round-trip.

NFR-25 self-summary instruction 이 prompt 에 박혀 있어야 함 (audit regression 의 동적 가드).
import-time assertion 도 같은 키워드를 검증 (이중 가드).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.collection.llm_search import SYSTEM_PROMPT_TEMPLATE, search_for_leaf
from app.llm_provider.mock import MockProvider, hash_prompt_search


class TestSystemPromptInstruction:
    def test_contains_self_summary_korean_instruction(self) -> None:
        # NFR-25 핵심 키워드
        assert "본인의 말로" in SYSTEM_PROMPT_TEMPLATE
        assert "1~2문장" in SYSTEM_PROMPT_TEMPLATE

    def test_contains_top_n_placeholder(self) -> None:
        assert "{top_n}" in SYSTEM_PROMPT_TEMPLATE

    def test_contains_dedup_hint(self) -> None:
        assert "중복 제거" in SYSTEM_PROMPT_TEMPLATE

    def test_contains_confidence_guidance(self) -> None:
        assert "confidence" in SYSTEM_PROMPT_TEMPLATE


class TestMockProviderSearchFixture:
    @pytest.mark.asyncio
    async def test_search_with_tools_reads_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # MockProvider 의 _FIXTURE_DIR 을 tmp_path 로 redirect
        from app.llm_provider import mock as mock_mod

        monkeypatch.setattr(mock_mod, "_FIXTURE_DIR", tmp_path)
        trace_json: dict[str, Any] = {"mode": "onboarding_fallback", "clusters": []}
        leaf_label = "Quantum Machine Learning"
        top_n = 3
        hash_val = hash_prompt_search(trace_json, leaf_label, top_n)
        fixture = tmp_path / f"search_{hash_val}.json"
        fixture.write_text(
            json.dumps(
                {
                    "model": "mock-search-high",
                    "results": [
                        {
                            "title": "QML Survey",
                            "url": "https://arxiv.org/abs/2401.01234",
                            "abstract_summary": "QML 동향을 본인 말로 요약했다.",
                            "publisher_domain": "arxiv.org",
                            "publisher_label": "arXiv",
                            "published_at": "2026-04-01T00:00:00Z",
                            "doi": "10.48550/arXiv.2401.01234",
                            "confidence": 0.9,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        # _concurrency.acquire_slot 우회 — RedisClient 의존 회피
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _noop_slot(_uid: object) -> Any:
            yield

        monkeypatch.setattr(mock_mod, "acquire_slot", _noop_slot)

        provider = MockProvider()
        results = await search_for_leaf(
            provider,
            trace_json=trace_json,
            leaf_label=leaf_label,
            parent_cso_topic_id=uuid4(),
            user_id=uuid4(),
            top_n=top_n,
        )
        assert len(results) == 1
        r = results[0]
        assert r.title == "QML Survey"
        assert r.publisher_domain == "arxiv.org"
        assert r.doi == "10.48550/arXiv.2401.01234"
        assert r.confidence == 0.9
