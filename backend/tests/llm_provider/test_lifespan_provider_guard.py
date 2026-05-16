"""lifespan _validate_llm_provider 가드 — Codex round 2 S-08.

A4 collection 은 mock + openai 만 지원. Anthropic/OpenRouter/CodexOAuth 는 boot 거부.
"""
from __future__ import annotations

import pytest

from app.contracts import LLMProviderType
from app.lifespan import _validate_llm_provider


class TestProviderGuard:
    def test_mock_passes(self) -> None:
        _validate_llm_provider(LLMProviderType.MOCK)  # no raise

    def test_openai_passes(self) -> None:
        _validate_llm_provider(LLMProviderType.OPENAI)  # no raise

    def test_anthropic_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="A4 collection 미지원"):
            _validate_llm_provider(LLMProviderType.ANTHROPIC)

    def test_openrouter_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="A4 collection 미지원"):
            _validate_llm_provider(LLMProviderType.OPENROUTER)

    def test_codex_oauth_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="A4 collection 미지원"):
            _validate_llm_provider(LLMProviderType.CODEX_OAUTH)
