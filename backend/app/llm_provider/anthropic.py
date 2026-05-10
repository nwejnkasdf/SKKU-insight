"""AnthropicAPIProvider — stub.

`LLM_PROVIDER=anthropic` 토글 시 사용. 1차 시연은 mock + openai 만 동작. 후속 작업으로 본문.
"""
from __future__ import annotations

from app.llm_provider.protocol import (
    ChatMessage,
    LLMResponse,
    ModelSlot,
    ResponseFormat,
)


class AnthropicAPIProvider:
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model_slot: ModelSlot,
        response_format: ResponseFormat = "text",
        max_tokens: int | None = None,
        temperature: float = 0.2,
        user_id: str | None = None,
    ) -> LLMResponse:
        raise NotImplementedError(
            "AnthropicAPIProvider 는 1차 미구현 — LLM_PROVIDER=mock 또는 openai 사용."
        )


__all__ = ["AnthropicAPIProvider"]
