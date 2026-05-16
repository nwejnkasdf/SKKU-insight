"""OpenAIAPIProvider — httpx async + 세마포어 + 토큰 budget.

env `OPENAI_API_KEY` 필수. `LLM_MODEL_HIGH` / `LLM_MODEL_MEDIUM` 모델 매핑.
1차 시연 default 는 mock — openai 는 LLM_PROVIDER=openai 토글 시.

v13 round 2 (2026-05-16) 사용자 결정:
- 모델 = `settings.LLM_MODEL_HIGH` (default GPT-5.5)
- `search_with_tools()` = **OpenAI Responses API web_search 도구 정식 호출**
  - endpoint `https://api.openai.com/v1/responses`
  - tools=[{"type":"web_search"}], tool_choice="auto"
  - reasoning 파라미터 미전송 — OpenAI default 에 위임 (사용자 결정)
  - 응답 `output[].content[]` 의 `type=output_text` 텍스트 → JSON parse → SearchResult list

`complete()` 는 기존 Chat Completions API 유지.
"""
from __future__ import annotations

from datetime import datetime
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
    ProviderError,
    ResponseFormat,
    SearchResult,
)
from app.redis import get_redis

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


class OpenAIAPIProvider:
    """OpenAI Chat Completions API (`complete`) + Responses API web_search (`search_with_tools`)."""

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

    async def search_with_tools(
        self,
        trace_json: dict[str, Any],
        leaf_label: str,
        *,
        top_n: int = 10,
        user_id: str | None = None,
    ) -> list[SearchResult]:
        """OpenAI Responses API + web_search 도구 정식 호출 (v13 round 2 C-01)."""
        # 본 구현은 import 순환을 피하기 위해 함수 내에서 import.
        from app.collection.llm_search import SYSTEM_PROMPT_TEMPLATE

        settings = get_settings()
        redis = get_redis("default")
        if not await check_token_budget(redis):
            raise LLMBudgetExceeded()

        import json as _json

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(top_n=top_n)
        user_prompt = _json.dumps(
            {"trace": trace_json, "leaf_label": leaf_label, "top_n": top_n},
            ensure_ascii=False,
        )
        # Responses API request body — web_search 도구. reasoning 파라미터는 미전송하여
        # OpenAI default 에 위임 (v13 round 2 사용자 결정).
        payload: dict[str, Any] = {
            "model": settings.LLM_MODEL_HIGH,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "tools": [{"type": "web_search"}],
            "tool_choice": "auto",
        }
        async with acquire_slot(user_id):
            async with httpx.AsyncClient(
                timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS
            ) as client:
                try:
                    response = await client.post(
                        OPENAI_RESPONSES_URL,
                        headers={
                            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    # (round 3 R2-S03) response.json() 도 try 안에 포함 — JSON decode
                    # 에러가 raw exception 으로 누출되지 않도록 ProviderError 로 wrap.
                    data: dict[str, Any] = response.json()
                except httpx.HTTPError as exc:
                    raise ProviderError(
                        f"openai search http error: {type(exc).__name__}: {exc!r}"
                    ) from exc
                except ValueError as exc:
                    # httpx response.json() 은 JSONDecodeError → ValueError subclass.
                    raise ProviderError(
                        f"openai search response body parse error: {type(exc).__name__}: {exc!r}"
                    ) from exc

        usage = data.get("usage", {})
        # Responses API 의 usage 필드명 — input_tokens / output_tokens
        prompt_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
        completion_tokens = int(
            usage.get("output_tokens", usage.get("completion_tokens", 0))
        )
        await record_token_usage(prompt_tokens + completion_tokens, redis)

        text = _extract_response_text(data)
        if not text:
            raise ProviderError("openai search response missing output text")
        try:
            parsed = _json.loads(text)
        except _json.JSONDecodeError as exc:
            raise ProviderError(f"openai search json parse error: {exc}") from exc
        results_raw = parsed.get("results", [])
        if not isinstance(results_raw, list):
            raise ProviderError("openai search response missing 'results' list")
        results: list[SearchResult] = []
        for item in results_raw[:top_n]:
            if not isinstance(item, dict):
                continue
            results.append(_parse_search_item(item))
        return results


def _extract_response_text(data: dict[str, Any]) -> str:
    """Responses API 의 output[].content[] 에서 output_text 합치기.

    응답 형식 (간략):
        {"output": [
            {"type": "message", "content": [
                {"type": "output_text", "text": "..."},
                ...
            ]},
            {"type": "web_search_call", ...}  # 도구 호출 결과는 별도
        ]}

    output_text 텍스트들을 순서대로 합쳐 반환. `output_text` 라는 top-level helper
    필드가 있으면 그것 우선 사용 (편의 단축).
    """
    direct = data.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    chunks: list[str] = []
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text":
                txt = content.get("text", "")
                if isinstance(txt, str):
                    chunks.append(txt)
    return "".join(chunks)


def _parse_search_item(item: dict[str, Any]) -> SearchResult:
    published_at_raw = item.get("published_at")
    published_at: datetime | None = None
    if isinstance(published_at_raw, str) and published_at_raw:
        normalized = published_at_raw.rstrip("Z")
        if published_at_raw.endswith("Z"):
            normalized = normalized + "+00:00"
        try:
            published_at = datetime.fromisoformat(normalized)
        except ValueError:
            published_at = None
    return SearchResult(
        title=str(item.get("title", "")),
        url=str(item.get("url", "")),
        abstract_summary=str(item.get("abstract_summary", "")),
        publisher_domain=item.get("publisher_domain"),
        publisher_label=item.get("publisher_label"),
        published_at=published_at,
        doi=item.get("doi"),
        canonical_url=item.get("canonical_url"),
        confidence=float(item.get("confidence", 0.8)),
        raw=dict(item.get("raw", {})),
    )


__all__ = ["OpenAIAPIProvider"]
