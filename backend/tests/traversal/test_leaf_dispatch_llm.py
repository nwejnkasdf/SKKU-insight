"""C-57 leaf_dispatch_llm 단위 테스트 — retract_reposition + split_dispatch.

LLM mock provider 응답 → decisions list 변환 + anchor 검증 + 실패 fallback 검증.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.llm_provider.protocol import (
    ChatMessage,
    LLMResponse,
    ModelSlot,
    ProviderError,
    ResponseFormat,
    SearchResult,
)
from app.traversal.leaf_dispatch_llm import (
    LeafSummary,
    call_retract_reposition,
    call_split_dispatch,
)


@dataclass
class _MockProvider:
    """provider.complete 만 mock — search_with_tools 는 unused."""

    response_json: dict[str, Any] | None = None
    raise_error: Exception | None = None

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
        if self.raise_error is not None:
            raise self.raise_error
        return LLMResponse(
            text="",
            model="mock",
            prompt_tokens=0,
            completion_tokens=0,
            parsed_json=self.response_json,
        )

    async def search_with_tools(
        self,
        trace_json: dict[str, Any],
        leaf_label: str,
        *,
        top_n: int = 10,
        user_id: str | None = None,
    ) -> list[SearchResult]:
        return []


# ============================================================
# retract_reposition
# ============================================================


@pytest.mark.asyncio
async def test_retract_remap_and_archive_round_trip() -> None:
    user_id = uuid4()
    new_tail = uuid4()
    leaves = [
        LeafSummary(leaf_id=uuid4(), label_ko="RAG"),
        LeafSummary(leaf_id=uuid4(), label_ko="Prompt Engineering"),
    ]
    provider = _MockProvider(
        response_json={
            "decisions": [
                {"leaf_id": str(leaves[0].leaf_id), "decision": "remap"},
                {"leaf_id": str(leaves[1].leaf_id), "decision": "archive"},
            ]
        }
    )
    result = await call_retract_reposition(
        provider,  # type: ignore[arg-type]
        user_id=user_id,
        retracted_label="LLM",
        new_path_labels=["AI", "NLP"],
        new_path_tail_cso=new_tail,
        leaves=leaves,
    )
    assert result is not None
    assert len(result) == 2
    by_leaf = {d["leaf_id"]: d for d in result}
    assert by_leaf[leaves[0].leaf_id]["decision"] == "remap"
    assert by_leaf[leaves[0].leaf_id]["new_cso_topic_id"] == new_tail
    assert by_leaf[leaves[1].leaf_id]["decision"] == "archive"
    assert by_leaf[leaves[1].leaf_id]["new_cso_topic_id"] is None


@pytest.mark.asyncio
async def test_retract_provider_error_returns_none() -> None:
    """ProviderError → None 반환 (caller 가 stub fallback)."""
    leaves = [LeafSummary(leaf_id=uuid4(), label_ko="X")]
    provider = _MockProvider(raise_error=ProviderError("oops"))
    result = await call_retract_reposition(
        provider,  # type: ignore[arg-type]
        user_id=uuid4(),
        retracted_label="L",
        new_path_labels=["A"],
        new_path_tail_cso=uuid4(),
        leaves=leaves,
    )
    assert result is None


@pytest.mark.asyncio
async def test_retract_rejects_unknown_leaf_id() -> None:
    """LLM 응답에 모르는 leaf_id 가 있으면 무시 (hallucination 차단)."""
    leaves = [LeafSummary(leaf_id=uuid4(), label_ko="X")]
    bogus = uuid4()
    provider = _MockProvider(
        response_json={
            "decisions": [{"leaf_id": str(bogus), "decision": "remap"}]
        }
    )
    result = await call_retract_reposition(
        provider,  # type: ignore[arg-type]
        user_id=uuid4(),
        retracted_label="L",
        new_path_labels=["A"],
        new_path_tail_cso=uuid4(),
        leaves=leaves,
    )
    assert result == []


@pytest.mark.asyncio
async def test_retract_rejects_invalid_decision_token() -> None:
    leaves = [LeafSummary(leaf_id=uuid4(), label_ko="X")]
    provider = _MockProvider(
        response_json={
            "decisions": [{"leaf_id": str(leaves[0].leaf_id), "decision": "delete"}]
        }
    )
    result = await call_retract_reposition(
        provider,  # type: ignore[arg-type]
        user_id=uuid4(),
        retracted_label="L",
        new_path_labels=["A"],
        new_path_tail_cso=uuid4(),
        leaves=leaves,
    )
    assert result == []


# ============================================================
# split_dispatch
# ============================================================


@pytest.mark.asyncio
async def test_split_source_and_new_round_trip() -> None:
    source_cso = uuid4()
    new_cso = uuid4()
    leaves = [
        LeafSummary(leaf_id=uuid4(), label_ko="RAG"),
        LeafSummary(leaf_id=uuid4(), label_ko="Translation"),
    ]
    provider = _MockProvider(
        response_json={
            "decisions": [
                {"leaf_id": str(leaves[0].leaf_id), "target_trace": "source"},
                {"leaf_id": str(leaves[1].leaf_id), "target_trace": "new"},
            ]
        }
    )
    result = await call_split_dispatch(
        provider,  # type: ignore[arg-type]
        user_id=uuid4(),
        fork_label="NLP",
        source_child_label="LLM",
        new_child_label="Translation",
        source_child_cso=source_cso,
        new_child_cso=new_cso,
        leaves=leaves,
    )
    assert result is not None
    by_leaf = {d["leaf_id"]: d for d in result}
    assert by_leaf[leaves[0].leaf_id]["target_trace"] == "source"
    assert by_leaf[leaves[0].leaf_id]["target_cso_topic_id"] == source_cso
    assert by_leaf[leaves[1].leaf_id]["target_trace"] == "new"
    assert by_leaf[leaves[1].leaf_id]["target_cso_topic_id"] == new_cso


@pytest.mark.asyncio
async def test_split_provider_error_returns_none() -> None:
    leaves = [LeafSummary(leaf_id=uuid4(), label_ko="X")]
    provider = _MockProvider(raise_error=ProviderError("nope"))
    result = await call_split_dispatch(
        provider,  # type: ignore[arg-type]
        user_id=uuid4(),
        fork_label="F",
        source_child_label="A",
        new_child_label="B",
        source_child_cso=uuid4(),
        new_child_cso=uuid4(),
        leaves=leaves,
    )
    assert result is None


@pytest.mark.asyncio
async def test_split_rejects_invalid_target_trace_token() -> None:
    leaves = [LeafSummary(leaf_id=uuid4(), label_ko="X")]
    provider = _MockProvider(
        response_json={
            "decisions": [
                {"leaf_id": str(leaves[0].leaf_id), "target_trace": "neither"}
            ]
        }
    )
    result = await call_split_dispatch(
        provider,  # type: ignore[arg-type]
        user_id=uuid4(),
        fork_label="F",
        source_child_label="A",
        new_child_label="B",
        source_child_cso=uuid4(),
        new_child_cso=uuid4(),
        leaves=leaves,
    )
    assert result == []


@pytest.mark.asyncio
async def test_empty_leaves_short_circuits_without_llm_call() -> None:
    """leaves=[] 면 LLM 호출 안 함 — provider.raise_error 라도 [] 반환."""
    provider = _MockProvider(raise_error=ProviderError("should not be called"))
    retract_result = await call_retract_reposition(
        provider,  # type: ignore[arg-type]
        user_id=uuid4(),
        retracted_label="L",
        new_path_labels=["A"],
        new_path_tail_cso=uuid4(),
        leaves=[],
    )
    split_result = await call_split_dispatch(
        provider,  # type: ignore[arg-type]
        user_id=uuid4(),
        fork_label="F",
        source_child_label="A",
        new_child_label="B",
        source_child_cso=uuid4(),
        new_child_cso=uuid4(),
        leaves=[],
    )
    assert retract_result == []
    assert split_result == []
