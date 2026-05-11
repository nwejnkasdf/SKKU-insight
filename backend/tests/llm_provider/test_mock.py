"""MockProvider 단위 테스트 — fixture hit/miss + 분산 semaphore 회귀 가드.

A4 prep — Redis 분산 semaphore (C-19) global/per-user cap, timeout LLMBudgetExceeded,
release floor 0. multi-worker 환경에서 LLM_MAX_CONCURRENT 글로벌 한도 보장.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import redis.asyncio as aioredis

from app.config import get_settings
from app.llm_provider._concurrency import (
    _acquire_slot_distributed,
    _release_slot_distributed,
)
from app.llm_provider.mock import MockProvider, _hash_prompt
from app.llm_provider.protocol import ChatMessage, FixtureNotFound, LLMBudgetExceeded


@pytest.fixture
def reset_settings_cache() -> Iterator[None]:
    """get_settings lru_cache 를 테스트 전후로 클리어 — env 변동 격리."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


# ============================================================
# A4 prep — Redis 분산 semaphore (C-19) 회귀 가드
# ============================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_acquire_slot_caps_global_concurrent(
    redis_client: aioredis.Redis,
    reset_settings_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_MAX_CONCURRENT=8 일 때 12 concurrent acquire → 정확히 8 성공 + 4 LLMBudgetExceeded.

    Redis Lua atomic 체크가 multi-worker 환경에서 global cap 을 강제하는지 검증.
    """
    monkeypatch.setenv("LLM_MAX_CONCURRENT", "8")
    monkeypatch.setenv("LLM_MAX_CONCURRENT_PER_USER", "20")
    monkeypatch.setenv("LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()

    user_ids = [uuid4() for _ in range(12)]
    results = await asyncio.gather(
        *(_acquire_slot_distributed(uid, redis_client) for uid in user_ids),
        return_exceptions=True,
    )
    successes = sum(1 for r in results if r is None)
    failures = sum(1 for r in results if isinstance(r, LLMBudgetExceeded))
    assert successes == 8, f"expected 8 successes, got {successes} ({results})"
    assert failures == 4, f"expected 4 budget-exceeded, got {failures}"

    global_count = int(await redis_client.get("llm:active:global") or 0)
    assert global_count == 8

    # cleanup — 성공했던 슬롯만 release. 실패한 user 의 카운터는 변동 없음.
    for uid, result in zip(user_ids, results, strict=True):
        if result is None:
            await _release_slot_distributed(uid, redis_client)
    assert int(await redis_client.get("llm:active:global") or 0) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_acquire_slot_caps_per_user(
    redis_client: aioredis.Redis,
    reset_settings_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_MAX_CONCURRENT_PER_USER=2 일 때 같은 user 3번째 acquire → timeout.
    다른 user 는 영향 없음."""
    monkeypatch.setenv("LLM_MAX_CONCURRENT", "100")
    monkeypatch.setenv("LLM_MAX_CONCURRENT_PER_USER", "2")
    monkeypatch.setenv("LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()

    user_a = uuid4()
    user_b = uuid4()

    await _acquire_slot_distributed(user_a, redis_client)
    await _acquire_slot_distributed(user_a, redis_client)
    with pytest.raises(LLMBudgetExceeded):
        await _acquire_slot_distributed(user_a, redis_client)

    # user_b 는 별도 카운터라 영향 없음 — 2회 모두 성공
    await _acquire_slot_distributed(user_b, redis_client)
    await _acquire_slot_distributed(user_b, redis_client)

    assert int(await redis_client.get(f"llm:active:user:{user_a}") or 0) == 2
    assert int(await redis_client.get(f"llm:active:user:{user_b}") or 0) == 2

    for _ in range(2):
        await _release_slot_distributed(user_a, redis_client)
        await _release_slot_distributed(user_b, redis_client)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_acquire_slot_timeout_raises_budget_exceeded(
    redis_client: aioredis.Redis,
    reset_settings_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """global cap 도달 후 새 acquire → 대기 ≥ 1초 + LLMBudgetExceeded."""
    monkeypatch.setenv("LLM_MAX_CONCURRENT", "1")
    monkeypatch.setenv("LLM_MAX_CONCURRENT_PER_USER", "10")
    monkeypatch.setenv("LLM_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()

    user_a = uuid4()
    user_b = uuid4()
    await _acquire_slot_distributed(user_a, redis_client)

    start = time.monotonic()
    with pytest.raises(LLMBudgetExceeded):
        await _acquire_slot_distributed(user_b, redis_client)
    elapsed = time.monotonic() - start
    assert elapsed >= 1.0, f"timeout was not enforced (elapsed={elapsed:.2f}s)"

    await _release_slot_distributed(user_a, redis_client)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_release_floor_zero(redis_client: aioredis.Redis) -> None:
    """카운터가 0 또는 부재 상태일 때 release → 음수로 떨어지지 않음 (Lua `> 0` guard)."""
    user_id = uuid4()
    for _ in range(3):
        await _release_slot_distributed(user_id, redis_client)
        await _release_slot_distributed(None, redis_client)

    global_val = await redis_client.get("llm:active:global")
    user_val = await redis_client.get(f"llm:active:user:{user_id}")
    assert global_val is None or int(global_val) >= 0
    assert user_val is None or int(user_val) >= 0
