"""single-flight lock — Lua atomic CAS + 8s polling fallback."""
from __future__ import annotations

import uuid

import pytest
import redis.asyncio as aioredis

from app.contracts import RedisKey


@pytest.mark.asyncio
async def test_lua_release_only_self_token(
    redis_client: aioredis.Redis,
) -> None:
    """Lua atomic CAS — 자기 token 일치 시만 DEL (§11.#5)."""
    from app.recommendation.service import _RELEASE_LOCK_LUA

    user_id = uuid.uuid4()
    lock_key = RedisKey.recommendation_build_lock(user_id)
    my_token = uuid.uuid4().hex
    other_token = uuid.uuid4().hex

    # acquire — my_token.
    await redis_client.set(lock_key, my_token, nx=True, ex=30)

    # 다른 token 으로 release 시도 — 거부 (return 0).
    result_other = await redis_client.eval(
        _RELEASE_LOCK_LUA, 1, lock_key, other_token
    )
    assert int(result_other) == 0
    # lock 여전히 살아있음.
    val = await redis_client.get(lock_key)
    assert val == my_token

    # 자기 token 으로 release — 성공.
    result_self = await redis_client.eval(
        _RELEASE_LOCK_LUA, 1, lock_key, my_token
    )
    assert int(result_self) == 1
    val_after = await redis_client.get(lock_key)
    assert val_after is None


@pytest.mark.asyncio
async def test_lua_release_no_lock_returns_zero(
    redis_client: aioredis.Redis,
) -> None:
    """lock 자체가 없으면 (TTL 만료 후) Lua CAS 도 noop (return 0). race-safe."""
    from app.recommendation.service import _RELEASE_LOCK_LUA

    user_id = uuid.uuid4()
    lock_key = RedisKey.recommendation_build_lock(user_id)
    my_token = uuid.uuid4().hex
    result = await redis_client.eval(_RELEASE_LOCK_LUA, 1, lock_key, my_token)
    assert int(result) == 0


@pytest.mark.asyncio
async def test_recommendation_lock_key_namespaced(
    redis_client: aioredis.Redis,
) -> None:
    """RedisKey.recommendation_build_lock(user_id) prefix 검증."""
    user_id = uuid.uuid4()
    lock_key = RedisKey.recommendation_build_lock(user_id)
    assert lock_key == f"lock:recommendation_build:{user_id}"
    assert lock_key.startswith("lock:recommendation_build:")
