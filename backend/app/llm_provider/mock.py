"""MockProvider — prompt_hash → tests/fixtures/mock_llm/{hash}.json.

deterministic fixture lookup. 시연 / CI 안정성 위해 외부 호출 0.
fixture 미존재 시 `FixtureNotFound` raise (CI 단순 에러로 잡힘).

fixture JSON 형식:
```json
{
  "text": "...",
  "parsed_json": {...},   // optional
  "model": "mock-high",
  "prompt_tokens": 123,
  "completion_tokens": 45,
  "finish_reason": "stop"
}
```
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.llm_provider.protocol import (
    ChatMessage,
    FixtureNotFound,
    LLMResponse,
    ModelSlot,
    ResponseFormat,
)

# backend/app/llm_provider/mock.py → backend/tests/fixtures/mock_llm/
_FIXTURE_DIR = (
    Path(__file__).parent.parent.parent / "tests" / "fixtures" / "mock_llm"
)


class MockProvider:
    """deterministic fixture lookup. 시연 default."""

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
        prompt_hash = _hash_prompt(messages, model_slot, response_format)
        fixture = _FIXTURE_DIR / f"{prompt_hash}.json"
        if not fixture.exists():
            raise FixtureNotFound(prompt_hash)
        data: dict[str, Any] = json.loads(fixture.read_text(encoding="utf-8"))
        settings = get_settings()
        model_name = settings.LLM_MODEL_HIGH if model_slot == "high" else settings.LLM_MODEL_MEDIUM
        return LLMResponse(
            text=str(data.get("text", "")),
            model=str(data.get("model", model_name)),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            finish_reason=str(data.get("finish_reason", "stop")),
            parsed_json=data.get("parsed_json"),
            meta={"prompt_hash": prompt_hash},
        )


def _hash_prompt(
    messages: list[ChatMessage],
    model_slot: str,
    response_format: str,
) -> str:
    """deterministic prompt hash. messages + model_slot + response_format 만."""
    canonical = json.dumps(
        {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "model_slot": model_slot,
            "response_format": response_format,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


__all__ = ["MockProvider"]
