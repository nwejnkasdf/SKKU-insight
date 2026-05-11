"""MockProvider 단위 테스트 — fixture hit/miss."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.llm_provider.mock import MockProvider, _hash_prompt
from app.llm_provider.protocol import ChatMessage, FixtureNotFound


@pytest.mark.asyncio
async def test_mock_fixture_miss_raises() -> None:
    provider = MockProvider()
    messages = [ChatMessage(role="user", content="hello, never matched")]
    with pytest.raises(FixtureNotFound):
        await provider.complete(messages, model_slot="medium")


@pytest.mark.asyncio
async def test_mock_fixture_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """fixture 파일을 동적 생성해 hit 시 정상 응답."""
    fixture_dir = Path(__file__).parent.parent / "fixtures" / "mock_llm"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    messages = [ChatMessage(role="user", content="deterministic-test-prompt")]
    prompt_hash = _hash_prompt(messages, "medium", "text")
    fixture_path = fixture_dir / f"{prompt_hash}.json"
    fixture_path.write_text(
        json.dumps(
            {
                "text": "mock-response",
                "model": "mock-medium",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "finish_reason": "stop",
            }
        ),
        encoding="utf-8",
    )
    try:
        provider = MockProvider()
        response = await provider.complete(messages, model_slot="medium")
        assert response.text == "mock-response"
        assert response.model == "mock-medium"
        assert response.prompt_tokens == 10
        assert response.completion_tokens == 5
    finally:
        fixture_path.unlink(missing_ok=True)
