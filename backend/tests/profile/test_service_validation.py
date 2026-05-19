"""A8-v2 service unit — generate_profile_payload 의 LLM 응답 검증 + CSO 매핑 가드.

DB / Redis 의존성 없는 unit test 만. fetch_profile_llm_input 과 upsert_user_profile 의
통합은 통합 시연 (docker compose) 으로 검증.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import networkx as nx
import pytest

from app.llm_provider.protocol import ChatMessage, LLMResponse, ProviderError
from app.profile.config_loader import ProfileGeneratorConfig
from app.profile.schemas import (
    ActiveTraceSummary,
    ArchivedTraceSummary,
    CSOTopicCandidate,
    ProfileLLMInput,
    UserProfilePayload,
)
from app.profile.service import generate_profile_payload


def _config() -> ProfileGeneratorConfig:
    return ProfileGeneratorConfig(
        cron_expr="0 19 * * *",
        archive_score_tail_min=0.6,
        generator_version="v1",
        input_archive_max=8,
        reincarnation_gap_days_min=7,
        lock_ttl_seconds=180,
        cache_ttl_seconds=3600,
    )


def _llm_input(cso_a: UUID, cso_b: UUID) -> ProfileLLMInput:
    return ProfileLLMInput(
        user_active_day_counter=30,
        active_traces=[
            ActiveTraceSummary(
                trace_id=uuid4(),
                path_labels=["A", "B"],
                path_cso_topic_ids=[cso_a, cso_b],
                score_tail=0.7,
                last_activity_active_day=28,
            )
        ],
        archived_traces=[
            ArchivedTraceSummary(
                trace_id=uuid4(),
                path_labels=["C"],
                path_cso_topic_ids=[uuid4()],
                score_tail_at_archive=0.83,
                last_activity_active_day=10,
                archived_at_active_day=15,
            )
        ],
        top_interest_states=[],
        recent_saved_topic_labels=[],
        recent_hidden_topic_labels=[],
        not_interested_topic_labels=[],
        cso_candidate_pool=[
            CSOTopicCandidate(cso_topic_id=cso_a, label="A"),
            CSOTopicCandidate(cso_topic_id=cso_b, label="B"),
        ],
    )


def _build_cso_graph(node_ids: list[UUID]) -> nx.DiGraph:
    graph: nx.DiGraph = nx.DiGraph()
    for nid in node_ids:
        graph.add_node(nid, label="dummy")
    return graph


def _valid_payload(bridge_cso: UUID, seed_cso: UUID) -> dict[str, Any]:
    return {
        "recent_signals_summary": "최근 흥미 변화 단순 요약.",
        "persistent_tendencies_summary": "장기 성향 요약.",
        "likely_dislikes_summary": "거부 패턴 요약.",
        "fusion_candidates": [
            {
                "from_archived": ["C"],
                "from_active": ["A", "B"],
                "bridge_label": "Bridge X",
                "bridge_cso_topic_id": str(bridge_cso),
                "bridge_reasoning": "두 영역이 만나는 새 학습 path 추론 결과.",
            }
        ],
        "deepening_seeds": [{"cso_topic_id": str(seed_cso), "label": "Deep S"}],
        "broadening_seeds": [{"cso_topic_id": str(seed_cso), "label": "Broad S"}],
    }


class _MockProvider:
    """LLMProvider Protocol 의 최소 구현 — complete 만."""

    def __init__(
        self,
        *,
        parsed_json: Any | None = None,
        raise_exc: Exception | None = None,
        text: str = "",
    ) -> None:
        self._parsed = parsed_json
        self._exc = raise_exc
        self._text = text
        self.calls: list[list[ChatMessage]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model_slot: str,
        response_format: str = "text",
        max_tokens: int | None = None,
        temperature: float = 0.2,
        user_id: str | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        if self._exc is not None:
            raise self._exc
        return LLMResponse(
            text=self._text or json.dumps(self._parsed or {}),
            model="mock-gpt-5.5",
            prompt_tokens=100,
            completion_tokens=200,
            parsed_json=self._parsed,
        )

    async def search_with_tools(  # pragma: no cover — 호출 안 됨
        self,
        trace_json: dict[str, Any],
        leaf_label: str,
        *,
        top_n: int = 10,
        user_id: str | None = None,
    ) -> list[Any]:
        raise NotImplementedError


@pytest.mark.asyncio
class TestGenerateProfilePayload:
    async def test_returns_none_on_provider_error(self) -> None:
        cso_a = uuid4()
        cso_b = uuid4()
        graph = _build_cso_graph([cso_a, cso_b])
        provider = _MockProvider(raise_exc=ProviderError("HTTP 500"))
        result = await generate_profile_payload(
            provider,
            graph,
            llm_input=_llm_input(cso_a, cso_b),
            config=_config(),
            user_id=uuid4(),
        )
        assert result is None

    async def test_returns_none_on_missing_parsed_json(self) -> None:
        cso_a = uuid4()
        cso_b = uuid4()
        graph = _build_cso_graph([cso_a, cso_b])
        provider = _MockProvider(parsed_json=None, text="free text only")
        result = await generate_profile_payload(
            provider,
            graph,
            llm_input=_llm_input(cso_a, cso_b),
            config=_config(),
            user_id=uuid4(),
        )
        assert result is None

    async def test_returns_none_on_schema_violation(self) -> None:
        cso_a = uuid4()
        cso_b = uuid4()
        graph = _build_cso_graph([cso_a, cso_b])
        provider = _MockProvider(parsed_json={"recent_signals_summary": "invalid"})
        result = await generate_profile_payload(
            provider,
            graph,
            llm_input=_llm_input(cso_a, cso_b),
            config=_config(),
            user_id=uuid4(),
        )
        assert result is None

    async def test_valid_payload_passes_through(self) -> None:
        cso_a = uuid4()
        cso_b = uuid4()
        graph = _build_cso_graph([cso_a, cso_b])
        payload_dict = _valid_payload(bridge_cso=cso_a, seed_cso=cso_b)
        provider = _MockProvider(parsed_json=payload_dict)
        result = await generate_profile_payload(
            provider,
            graph,
            llm_input=_llm_input(cso_a, cso_b),
            config=_config(),
            user_id=uuid4(),
        )
        assert isinstance(result, UserProfilePayload)
        assert len(result.fusion_candidates) == 1
        assert result.fusion_candidates[0].bridge_cso_topic_id == cso_a

    async def test_bridge_cso_not_in_graph_drops_candidate(self) -> None:
        """LLM hallucination 가드 — graph 부재 ID 의 candidate 만 제거."""
        cso_a = uuid4()
        cso_b = uuid4()
        unknown_cso = uuid4()
        graph = _build_cso_graph([cso_a, cso_b])  # unknown_cso 부재
        payload_dict = _valid_payload(bridge_cso=unknown_cso, seed_cso=cso_b)
        provider = _MockProvider(parsed_json=payload_dict)
        result = await generate_profile_payload(
            provider,
            graph,
            llm_input=_llm_input(cso_a, cso_b),
            config=_config(),
            user_id=uuid4(),
        )
        assert result is not None
        # 위반 candidate 제거 — fusion 0건, seeds 1건 (cso_b 는 graph 안).
        assert result.fusion_candidates == []
        assert len(result.deepening_seeds) == 1
        assert result.deepening_seeds[0].cso_topic_id == cso_b

    async def test_seed_cso_not_in_graph_drops_seed(self) -> None:
        cso_a = uuid4()
        cso_b = uuid4()
        unknown_seed = uuid4()
        graph = _build_cso_graph([cso_a, cso_b])
        payload_dict = _valid_payload(bridge_cso=cso_a, seed_cso=unknown_seed)
        provider = _MockProvider(parsed_json=payload_dict)
        result = await generate_profile_payload(
            provider,
            graph,
            llm_input=_llm_input(cso_a, cso_b),
            config=_config(),
            user_id=uuid4(),
        )
        assert result is not None
        # fusion 은 valid, seeds 는 unknown 으로 제거.
        assert len(result.fusion_candidates) == 1
        assert result.deepening_seeds == []
        assert result.broadening_seeds == []

    async def test_passes_high_slot_and_json_response_format(self) -> None:
        cso_a = uuid4()
        cso_b = uuid4()
        graph = _build_cso_graph([cso_a, cso_b])
        provider = _MockProvider(
            parsed_json=_valid_payload(bridge_cso=cso_a, seed_cso=cso_b)
        )
        await generate_profile_payload(
            provider,
            graph,
            llm_input=_llm_input(cso_a, cso_b),
            config=_config(),
            user_id=uuid4(),
        )
        # complete 호출 시 messages 가 system + user 2건이고, model_slot 은 high.
        assert len(provider.calls) == 1
        messages = provider.calls[0]
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[1].role == "user"
