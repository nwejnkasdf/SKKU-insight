"""MockProvider — prompt_hash → tests/fixtures/mock_llm/{hash}.json.

deterministic fixture lookup. 시연 / CI 안정성 위해 외부 호출 0.
fixture 미존재 시 `FixtureNotFound` raise (CI 단순 에러로 잡힘).

complete() fixture 형식:
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

search_with_tools() fixture 형식 (`search_{hash}.json`):
```json
{
  "model": "mock-search-high",
  "prompt_tokens": 200,
  "completion_tokens": 600,
  "results": [
    {
      "title": "...",
      "url": "https://arxiv.org/abs/...",
      "abstract_summary": "본인 말로 요약 (NFR-25)",
      "publisher_domain": "arxiv.org",
      "publisher_label": "arXiv",
      "published_at": "2026-04-01T00:00:00Z",
      "doi": "10.48550/arXiv.2604.01234",
      "canonical_url": "https://arxiv.org/abs/2604.01234",
      "confidence": 0.85,
      "raw": {"trust_hint": "academic"}
    }
  ]
}
```
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.llm_provider._concurrency import acquire_slot
from app.llm_provider.protocol import (
    ChatMessage,
    FixtureNotFound,
    LLMResponse,
    ModelSlot,
    ResponseFormat,
    SearchResult,
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

    async def search_with_tools(
        self,
        trace_json: dict[str, Any],
        leaf_label: str,
        *,
        top_n: int = 10,
        user_id: str | None = None,
    ) -> list[SearchResult]:
        prompt_hash = hash_prompt_search(trace_json, leaf_label, top_n)
        fixture = _FIXTURE_DIR / f"search_{prompt_hash}.json"
        if not fixture.exists():
            raise FixtureNotFound(prompt_hash)
        async with acquire_slot(user_id):
            data: dict[str, Any] = json.loads(
                fixture.read_text(encoding="utf-8")
            )
        results_raw = data.get("results", [])
        results: list[SearchResult] = []
        for item in results_raw[:top_n]:
            results.append(_parse_search_item(item))
        return results


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


def hash_prompt_search(
    trace_json: dict[str, Any],
    leaf_label: str,
    top_n: int,
) -> str:
    """deterministic search prompt hash. fixture lookup key.

    테스트가 동일 함수로 fixture 파일명 계산 → CI 안정.

    (Codex round 2 N-03 + round 3 R2-N01) `SYSTEM_PROMPT_VERSION` (human-readable)
    **+ SYSTEM_PROMPT_TEMPLATE 본문 SHA256** 둘 다 포함 → prompt 본문 1자라도 변경되면
    수동 bump 없이도 fixture 자동 invalidate (수동 bump 누락 silent pass 차단).
    """
    # 순환 import 회피 — 함수 내부 import.
    from app.collection.llm_search import (
        SYSTEM_PROMPT_TEMPLATE,
        SYSTEM_PROMPT_VERSION,
    )

    prompt_body_hash = hashlib.sha256(
        SYSTEM_PROMPT_TEMPLATE.encode("utf-8")
    ).hexdigest()[:16]

    canonical = json.dumps(
        {
            "trace": trace_json,
            "leaf_label": leaf_label,
            "top_n": top_n,
            "prompt_version": SYSTEM_PROMPT_VERSION,
            "prompt_body_hash": prompt_body_hash,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _parse_search_item(item: dict[str, Any]) -> SearchResult:
    published_at_raw = item.get("published_at")
    published_at: datetime | None = None
    if isinstance(published_at_raw, str) and published_at_raw:
        # ISO8601 — strip trailing Z (datetime.fromisoformat py 3.11+ 도 Z 미지원).
        normalized = published_at_raw.rstrip("Z")
        if published_at_raw.endswith("Z"):
            normalized = normalized + "+00:00"
        published_at = datetime.fromisoformat(normalized)
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


__all__ = ["MockProvider", "hash_prompt_search"]
