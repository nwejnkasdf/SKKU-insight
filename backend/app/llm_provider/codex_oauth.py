"""CodexOAuthProvider — local experimental stub.

비공식 OAuth 세션 토큰 사용. 본인 토이 빌드 / 로컬 실험 전용. 시연 default 가 아니다.
1차 미구현.
"""
from __future__ import annotations

from typing import Any

from app.llm_provider.protocol import (
    ChatMessage,
    LLMResponse,
    ModelSlot,
    ResponseFormat,
    SearchResult,
)


class CodexOAuthProvider:
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
            "CodexOAuthProvider 는 local experimental — 1차 미구현."
        )

    async def search_with_tools(
        self,
        trace_json: dict[str, Any],
        leaf_label: str,
        *,
        top_n: int = 10,
        user_id: str | None = None,
    ) -> list[SearchResult]:
        raise NotImplementedError(
            "CodexOAuthProvider.search_with_tools 는 local experimental — 1차 미구현."
        )


__all__ = ["CodexOAuthProvider"]
