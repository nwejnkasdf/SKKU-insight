"""C-73 fusion bridge LLM 선택 테스트 — 닫힌 목록 선택 + 거부 일급 + 가드 4분기.

no DB. FakeProvider 가 canned LLMResponse 반환.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.llm_provider.protocol import (
    ChatMessage,
    FixtureNotFound,
    LLMResponse,
    ProviderError,
)
from app.traversal.fusion_select_llm import (
    BridgeOption,
    call_fusion_bridge_select,
)


class FakeProvider:
    """canned 응답 또는 예외를 반환하는 최소 provider."""

    def __init__(
        self,
        *,
        parsed_json: Any | None = None,
        text: str = "",
        raises: Exception | None = None,
    ) -> None:
        self._parsed_json = parsed_json
        self._text = text
        self._raises = raises
        self.last_messages: list[ChatMessage] | None = None

    async def complete(self, **kwargs: Any) -> LLMResponse:
        self.last_messages = kwargs.get("messages")
        if self._raises is not None:
            raise self._raises
        return LLMResponse(
            text=self._text,
            model="fake",
            prompt_tokens=0,
            completion_tokens=0,
            parsed_json=self._parsed_json,
        )


def _options(n: int = 3) -> list[BridgeOption]:
    return [
        BridgeOption(cso_topic_id=uuid.uuid4(), label=f"topic_{i}") for i in range(n)
    ]


_PATHS = {
    "archived_path_labels": ["AI", "NLP", "LLM"],
    "active_path_labels": ["Systems", "OS", "Scheduling"],
}


class TestValidSelection:
    async def test_valid_pick_returned_with_reasoning(self) -> None:
        options = _options()
        chosen = options[1]
        provider = FakeProvider(
            parsed_json={
                "bridge_cso_topic_id": str(chosen.cso_topic_id),
                "reasoning": "두 영역의 지식이 실제로 만나는 교차 토픽이라 선택.",
            }
        )
        selection = await call_fusion_bridge_select(
            provider,  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            options=options,
            **_PATHS,
        )
        assert selection is not None
        assert selection.cso_topic_id == chosen.cso_topic_id
        assert "교차" in selection.reasoning

    async def test_parses_text_when_parsed_json_missing(self) -> None:
        options = _options()
        chosen = options[0]
        provider = FakeProvider(
            text=(
                f'{{"bridge_cso_topic_id": "{chosen.cso_topic_id}", '
                f'"reasoning": "텍스트 본문 JSON 경로 검증."}}'
            )
        )
        selection = await call_fusion_bridge_select(
            provider,  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            options=options,
            **_PATHS,
        )
        assert selection is not None
        assert selection.cso_topic_id == chosen.cso_topic_id

    async def test_prompt_contains_all_candidate_ids(self) -> None:
        options = _options(4)
        provider = FakeProvider(
            parsed_json={
                "bridge_cso_topic_id": str(options[0].cso_topic_id),
                "reasoning": "후보 노출 검증용 더미 선택 이유 텍스트.",
            }
        )
        await call_fusion_bridge_select(
            provider,  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            options=options,
            **_PATHS,
        )
        assert provider.last_messages is not None
        user_msg = provider.last_messages[-1].content
        for opt in options:
            assert str(opt.cso_topic_id) in user_msg


class TestRefusalIsFirstClass:
    async def test_explicit_null_refusal_returns_none(self) -> None:
        provider = FakeProvider(
            parsed_json={
                "bridge_cso_topic_id": None,
                "reasoning": "어느 후보도 두 영역의 의미 있는 교차가 아님.",
            }
        )
        assert (
            await call_fusion_bridge_select(
                provider,  # type: ignore[arg-type]
                user_id=uuid.uuid4(),
                options=_options(),
                **_PATHS,
            )
            is None
        )

    async def test_empty_options_short_circuit_none(self) -> None:
        provider = FakeProvider(parsed_json={})
        assert (
            await call_fusion_bridge_select(
                provider,  # type: ignore[arg-type]
                user_id=uuid.uuid4(),
                options=[],
                **_PATHS,
            )
            is None
        )
        # 후보가 없으면 LLM 호출 자체를 하지 않음
        assert provider.last_messages is None


class TestHallucinationGuards:
    async def test_out_of_pool_id_treated_as_refusal(self) -> None:
        provider = FakeProvider(
            parsed_json={
                "bridge_cso_topic_id": str(uuid.uuid4()),  # 풀 밖 ID
                "reasoning": "환각 ID 선택 — 가드가 거부해야 함.",
            }
        )
        assert (
            await call_fusion_bridge_select(
                provider,  # type: ignore[arg-type]
                user_id=uuid.uuid4(),
                options=_options(),
                **_PATHS,
            )
            is None
        )

    async def test_malformed_uuid_treated_as_refusal(self) -> None:
        provider = FakeProvider(
            parsed_json={"bridge_cso_topic_id": "not-a-uuid", "reasoning": "x" * 30}
        )
        assert (
            await call_fusion_bridge_select(
                provider,  # type: ignore[arg-type]
                user_id=uuid.uuid4(),
                options=_options(),
                **_PATHS,
            )
            is None
        )

    @pytest.mark.parametrize("bad_text", ["not json at all", "[1, 2, 3]"])
    async def test_unparseable_or_non_dict_treated_as_refusal(
        self, bad_text: str
    ) -> None:
        provider = FakeProvider(text=bad_text)
        assert (
            await call_fusion_bridge_select(
                provider,  # type: ignore[arg-type]
                user_id=uuid.uuid4(),
                options=_options(),
                **_PATHS,
            )
            is None
        )


class TestProviderFailures:
    @pytest.mark.parametrize(
        "exc",
        [ProviderError("boom"), FixtureNotFound("no fixture")],
    )
    async def test_provider_errors_treated_as_refusal(self, exc: Exception) -> None:
        provider = FakeProvider(raises=exc)
        assert (
            await call_fusion_bridge_select(
                provider,  # type: ignore[arg-type]
                user_id=uuid.uuid4(),
                options=_options(),
                **_PATHS,
            )
            is None
        )


class TestReasoningClamp:
    async def test_reasoning_truncated_to_300(self) -> None:
        options = _options()
        provider = FakeProvider(
            parsed_json={
                "bridge_cso_topic_id": str(options[0].cso_topic_id),
                "reasoning": "가" * 500,
            }
        )
        selection = await call_fusion_bridge_select(
            provider,  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            options=options,
            **_PATHS,
        )
        assert selection is not None
        assert len(selection.reasoning) == 300
