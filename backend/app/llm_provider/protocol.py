"""LLMProvider Protocol + 공통 dataclass.

`model_slot` 의 값은 `"high"` (동적 리프 생성·병합) 또는 `"medium"` (요약·추천 이유).
`LLMResponse.parsed_json` 은 response_format="json" 호출 시 채워짐.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ModelSlot = Literal["high", "medium"]
ResponseFormat = Literal["text", "json"]


@dataclass(slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str = "stop"
    parsed_json: Any | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class FixtureNotFound(Exception):
    """MockProvider 가 prompt_hash 에 해당하는 fixture 를 찾지 못함."""

    def __init__(self, prompt_hash: str) -> None:
        super().__init__(f"mock fixture not found: {prompt_hash}")
        self.prompt_hash = prompt_hash


class LLMBudgetExceeded(Exception):
    """LLM_DAILY_TOKEN_BUDGET 초과. fallback 경로 진입 신호."""


class LLMProvider(Protocol):
    """모든 provider 가 따라야 할 Protocol."""

    async def complete(  # type: ignore[empty-body]
        self,
        messages: list[ChatMessage],
        *,
        model_slot: ModelSlot,
        response_format: ResponseFormat = "text",
        max_tokens: int | None = None,
        temperature: float = 0.2,
        user_id: str | None = None,
    ) -> LLMResponse: ...


__all__ = [
    "ChatMessage",
    "FixtureNotFound",
    "LLMBudgetExceeded",
    "LLMProvider",
    "LLMResponse",
    "ModelSlot",
    "ResponseFormat",
]
