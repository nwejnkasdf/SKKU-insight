"""LLMProvider 추상 + 5 구현체.

env `LLM_PROVIDER` 토글로 인스턴스 선택. 시연 default 는 mock (deterministic fixture).
실제 LLM 호출이 필요한 시점부터 openai 로 전환.

- Protocol: protocol.py
- 동시성: _concurrency.py (전역 + per-user semaphore + Redis 토큰 budget)
- 구현체:
  - MockProvider: prompt_hash → backend/tests/fixtures/mock_llm/{hash}.json
  - OpenAIAPIProvider: httpx async, /v1/chat/completions
  - Anthropic / OpenRouter / CodexOAuth: stub NotImplementedError (후속 작업)
"""
from __future__ import annotations

from app.contracts import LLMProviderType
from app.llm_provider.anthropic import AnthropicAPIProvider
from app.llm_provider.codex_oauth import CodexOAuthProvider
from app.llm_provider.mock import MockProvider
from app.llm_provider.openai import OpenAIAPIProvider
from app.llm_provider.openrouter import OpenRouterProvider
from app.llm_provider.protocol import (
    ChatMessage,
    FixtureNotFound,
    LLMBudgetExceeded,
    LLMProvider,
    LLMResponse,
    ProviderError,
    SearchResult,
)


def get_provider(provider_type: LLMProviderType | str) -> LLMProvider:
    """`LLM_PROVIDER` env 값에 따라 인스턴스 반환."""
    if isinstance(provider_type, str):
        provider_type = LLMProviderType(provider_type)
    if provider_type == LLMProviderType.MOCK:
        return MockProvider()
    if provider_type == LLMProviderType.OPENAI:
        return OpenAIAPIProvider()
    if provider_type == LLMProviderType.ANTHROPIC:
        return AnthropicAPIProvider()
    if provider_type == LLMProviderType.OPENROUTER:
        return OpenRouterProvider()
    if provider_type == LLMProviderType.CODEX_OAUTH:
        return CodexOAuthProvider()
    raise ValueError(f"unknown LLM_PROVIDER: {provider_type}")


__all__ = [
    "AnthropicAPIProvider",
    "ChatMessage",
    "CodexOAuthProvider",
    "FixtureNotFound",
    "LLMBudgetExceeded",
    "LLMProvider",
    "LLMResponse",
    "MockProvider",
    "OpenAIAPIProvider",
    "OpenRouterProvider",
    "ProviderError",
    "SearchResult",
    "get_provider",
]
