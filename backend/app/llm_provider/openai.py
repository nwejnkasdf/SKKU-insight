"""OpenAIAPIProvider — httpx async + 세마포어 + 토큰 budget.

env `OPENAI_API_KEY` 필수. `LLM_MODEL_HIGH` / `LLM_MODEL_MEDIUM` 모델 매핑.
1차 시연 default 는 mock — openai 는 LLM_PROVIDER=openai 토글 시.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.llm_provider._concurrency import (
    acquire_slot,
    check_token_budget,
    record_token_usage,
)
from app.llm_provider.protocol import (
    ChatMessage,
    LLMBudgetExceeded,
    LLMResponse,
    ModelSlot,
    ResponseFormat,
)
from app.redis import get_redis

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIAPIProvider:
    """OpenAI Chat Completions API 호출."""

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
        settings = get_settings()
        redis = get_redis("default")
        if not await check_token_budget(redis):
            raise LLMBudgetExceeded()

        model_name = (
            settings.LLM_MODEL_HIGH if model_slot == "high" else settings.LLM_MODEL_MEDIUM
        )
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        async with acquire_slot(user_id):
            async with httpx.AsyncClient(
                timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    OPENAI_CHAT_URL,
                    headers={
                        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
        choice = data["choices"][0]
        message = choice["message"]
        text = str(message.get("content", ""))
        usage = data.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        await record_token_usage(prompt_tokens + completion_tokens, redis)

        parsed_json = None
        if response_format == "json":
            import json

            try:
                parsed_json = json.loads(text)
            except json.JSONDecodeError:
                parsed_json = None
        return LLMResponse(
            text=text,
            model=str(data.get("model", model_name)),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=str(choice.get("finish_reason", "stop")),
            parsed_json=parsed_json,
            meta={"id": data.get("id")},
        )


__all__ = ["OpenAIAPIProvider"]
