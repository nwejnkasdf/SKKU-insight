"""LLMProvider Protocol + 공통 dataclass.

`model_slot` 의 값은 `"high"` (동적 리프 생성·병합) 또는 `"medium"` (요약·추천 이유).
`LLMResponse.parsed_json` 은 response_format="json" 호출 시 채워짐.

v13 라운드 (2026-05-11) A4 Topic-driven Pivot 으로 `search_with_tools()` 메서드 추가.
LLM 이 web 검색 도구로 자료 fetch → SearchResult list 반환.
- MockProvider: search_{hash}.json fixture lookup
- OpenAI/Anthropic: web_search tool prompt-based 호출
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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


@dataclass(slots=True)
class SearchResult:
    """`search_with_tools()` 단일 결과. Document INSERT 직전 변환.

    - `abstract_summary` 는 NFR-25 정합 = LLM self-summary (외부 원문 복사 금지).
    - `confidence` default 0.8 — DocumentTopic.confidence 로 그대로 사용.
    - `raw` 는 Document.raw JSONB 에 저장 (publisher 정보 포함).
    """

    title: str
    url: str
    abstract_summary: str
    publisher_domain: str | None = None
    publisher_label: str | None = None
    published_at: datetime | None = None
    doi: str | None = None
    canonical_url: str | None = None
    confidence: float = 0.8
    raw: dict[str, Any] = field(default_factory=dict)


class FixtureNotFound(Exception):
    """MockProvider 가 prompt_hash 에 해당하는 fixture 를 찾지 못함."""

    def __init__(self, prompt_hash: str) -> None:
        super().__init__(f"mock fixture not found: {prompt_hash}")
        self.prompt_hash = prompt_hash


class LLMBudgetExceeded(Exception):
    """LLM_DAILY_TOKEN_BUDGET 초과. fallback 경로 진입 신호."""


class ProviderError(Exception):
    """LLM provider 호출 실패 (네트워크/HTTP/응답 형식). orchestrator 가 CollectionJob.failure_reason 으로 변환."""


class LLMProvider(Protocol):
    """모든 provider 가 따라야 할 Protocol."""

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model_slot: ModelSlot,
        response_format: ResponseFormat = "text",
        max_tokens: int | None = None,
        temperature: float = 0.2,
        user_id: str | None = None,
    ) -> LLMResponse: ...

    async def search_with_tools(
        self,
        trace_json: dict[str, Any],
        leaf_label: str,
        *,
        top_n: int = 10,
        user_id: str | None = None,
    ) -> list[SearchResult]:
        """v13 pivot: LLM 이 web 검색 도구로 자료 fetch.

        - trace_json: user trace + 선택 cluster 정보 (LLM 자율 query 결정 input)
        - leaf_label: 검색 의도 강조용 토픽 라벨
        - top_n: 최대 결과 수 (default 10)
        - user_id: 동시성 가드 (`acquire_slot(user_id)`) 용
        """
        ...


__all__ = [
    "ChatMessage",
    "FixtureNotFound",
    "LLMBudgetExceeded",
    "LLMProvider",
    "LLMResponse",
    "ModelSlot",
    "ProviderError",
    "ResponseFormat",
    "SearchResult",
]
