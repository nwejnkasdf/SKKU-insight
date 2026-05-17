"""trace_anchor_required 위반 retry 흐름 (A7 결정 #15).

identify_emerging_with_validation:
1. 1차 LLM 호출 → 응답 중 일부 candidate 의 cso_topic_ids 가 anchor 외 → 위반 candidate 거부.
2. anchor 위반 발생 시 보강된 prompt (위반 노드 list) 로 2차 호출 (retry cap=1).
3. 2차 호출도 anchor 위반이면 빈 응답 fallback + warning log.

본 테스트는 LLMProvider 를 AsyncMock 으로 대체 — fixture 의존 없이 retry 흐름 검증.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import networkx as nx
import pytest

from app.contracts import LeafTopicStatus, TraversalStatus
from app.leaf_lifecycle.llm_identifier import (
    identify_emerging_with_validation,
)
from app.llm_provider.protocol import LLMResponse


def _make_response(candidates: list[dict[str, Any]]) -> LLMResponse:
    return LLMResponse(
        text="",
        model="mock-high",
        prompt_tokens=100,
        completion_tokens=50,
        finish_reason="stop",
        parsed_json={"candidates": candidates},
        meta={},
    )


def _mock_trace(path: list[uuid.UUID]):
    t = MagicMock()
    t.trace_id = uuid.uuid4()
    t.user_id = uuid.uuid4()
    t.path = path
    t.status = TraversalStatus.ACTIVE.value
    return t


def _mock_leaf(label: str):
    leaf = MagicMock()
    leaf.leaf_topic_id = uuid.uuid4()
    leaf.label = label
    leaf.status = LeafTopicStatus.ACTIVE.value
    return leaf


@pytest.mark.asyncio
async def test_no_retry_when_all_accepted() -> None:
    """1차 응답 모두 accept → retry 없음."""
    root = uuid.uuid4()
    child = uuid.uuid4()
    graph: nx.DiGraph = nx.DiGraph()
    graph.add_edge(child, root)  # graph 컨벤션: child → parent
    trace = _mock_trace([root])

    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=_make_response(
            [
                {
                    "label_ko": "신규 토픽 A",
                    "label_en": "New A",
                    "cso_topic_ids": [str(child)],
                    "supporting_document_ids": [
                        str(uuid.uuid4()) for _ in range(5)
                    ],
                    "confidence": 0.8,
                    "rationale": "test",
                },
            ]
        )
    )

    results = await identify_emerging_with_validation(
        db=MagicMock(),
        provider=provider,
        graph=graph,
        user_id=uuid.uuid4(),
        new_documents=[uuid.uuid4() for _ in range(3)],
        existing_leaves=[],
        active_traces=[trace],
    )
    assert len(results) == 1
    assert results[0].accepted is True
    # 1차만 호출, retry 없음.
    assert provider.complete.await_count == 1


@pytest.mark.asyncio
async def test_retry_when_anchor_violation_and_recovers() -> None:
    """1차 anchor 위반 → 2차 호출 (보강된 prompt) → accept."""
    root = uuid.uuid4()
    child = uuid.uuid4()
    outsider = uuid.uuid4()
    graph: nx.DiGraph = nx.DiGraph()
    graph.add_edge(child, root)  # graph 컨벤션: child → parent
    graph.add_node(outsider)
    trace = _mock_trace([root])

    provider = MagicMock()
    docs = [str(uuid.uuid4()) for _ in range(5)]
    # 1차 응답: anchor 외 outsider 사용 → 거부.
    first_response = _make_response(
        [
            {
                "label_ko": "위반 토픽",
                "label_en": "Violating",
                "cso_topic_ids": [str(outsider)],
                "supporting_document_ids": docs,
                "confidence": 0.8,
                "rationale": "test",
            }
        ]
    )
    # 2차 응답: child (path 자손) 사용 → accept.
    second_response = _make_response(
        [
            {
                "label_ko": "정상 토픽",
                "label_en": "Valid",
                "cso_topic_ids": [str(child)],
                "supporting_document_ids": docs,
                "confidence": 0.8,
                "rationale": "test",
            }
        ]
    )
    provider.complete = AsyncMock(side_effect=[first_response, second_response])

    results = await identify_emerging_with_validation(
        db=MagicMock(),
        provider=provider,
        graph=graph,
        user_id=uuid.uuid4(),
        new_documents=[uuid.uuid4() for _ in range(3)],
        existing_leaves=[],
        active_traces=[trace],
    )
    # retry 후 results 는 2차 응답으로 교체됨 — accept 1건.
    assert provider.complete.await_count == 2
    accepted = [r for r in results if r.accepted]
    assert len(accepted) == 1
    assert accepted[0].candidate.label_ko == "정상 토픽"


@pytest.mark.asyncio
async def test_retry_cap_exhausted_returns_rejected() -> None:
    """1차/2차 모두 anchor 위반 → retry cap=1 소진 후 results 는 위반 candidate (rejected)."""
    root = uuid.uuid4()
    outsider = uuid.uuid4()
    graph: nx.DiGraph = nx.DiGraph()
    graph.add_node(root)
    graph.add_node(outsider)
    trace = _mock_trace([root])

    provider = MagicMock()
    docs = [str(uuid.uuid4()) for _ in range(5)]
    violation_response = _make_response(
        [
            {
                "label_ko": "위반 토픽",
                "label_en": "Violating",
                "cso_topic_ids": [str(outsider)],
                "supporting_document_ids": docs,
                "confidence": 0.8,
                "rationale": "test",
            }
        ]
    )
    provider.complete = AsyncMock(
        side_effect=[violation_response, violation_response]
    )

    results = await identify_emerging_with_validation(
        db=MagicMock(),
        provider=provider,
        graph=graph,
        user_id=uuid.uuid4(),
        new_documents=[uuid.uuid4() for _ in range(3)],
        existing_leaves=[],
        active_traces=[trace],
    )
    # 1차 + 2차 호출 후 retry cap 소진.
    assert provider.complete.await_count == 2
    # results 는 2차 응답의 reject candidate (caller 가 accepted=True 만 필터).
    accepted = [r for r in results if r.accepted]
    assert accepted == []
