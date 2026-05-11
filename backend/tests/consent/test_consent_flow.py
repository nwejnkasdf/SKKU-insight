"""consent 통합 — GET / POST / revoke 흐름 + onboarding 게이팅.

A4 prep — 누락된 회귀 가드 (agreed=false 거부 / revoke 시 추천 캐시 invalidate /
ConsentGate 가 onboarding 외 protected endpoint 도 차단 / account-deletion lock
지속성 / account-deletion 시 refresh 일괄 폐기).
"""
from __future__ import annotations

from typing import Any

import pytest
import redis.asyncio as aioredis
from httpx import AsyncClient


async def _signup_login(client: AsyncClient, email: str) -> str:
    password = "Strong-Test-Password-2026"
    await client.post("/auth/signup", json={"email": email, "password": password})
    resp = await client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    return resp.json()["access_token"]


async def _signup_login_tokens(
    client: AsyncClient, email: str
) -> tuple[str, str]:
    password = "Strong-Test-Password-2026"
    await client.post("/auth/signup", json={"email": email, "password": password})
    resp = await client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    tokens = resp.json()
    return tokens["access_token"], tokens["refresh_token"]


async def _grant_consent(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    resp = await client.post(
        "/consent",
        headers=headers,
        json={"consent_type": "personalization", "agreed": True},
    )
    assert resp.status_code == 201, resp.text


def _patch_rq_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """RQ enqueue no-op — real worker process 차단."""
    from app.consent import service as consent_service

    def _noop_enqueue(**kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(
        consent_service, "_enqueue_account_deletion", _noop_enqueue
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_consent_register_revoke(client: AsyncClient) -> None:
    access = await _signup_login(client, "consent-flow@example.com")
    headers = {"Authorization": f"Bearer {access}"}

    # 1) GET — 빈 상태
    resp = await client.get("/consent", headers=headers)
    assert resp.status_code == 200
    state = resp.json()
    assert state["active"] is False
    assert state["records"] == []

    # 2) POST — 등록
    resp = await client.post(
        "/consent",
        headers=headers,
        json={"consent_type": "personalization", "agreed": True},
    )
    assert resp.status_code == 201
    assert resp.json()["active"] is True

    # 3) revoke
    resp = await client.post(
        "/consent/revoke",
        headers=headers,
        json={"consent_type": "personalization", "confirmation": "confirm"},
    )
    assert resp.status_code == 200
    assert resp.json()["active"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_onboarding_gated_by_consent(client: AsyncClient) -> None:
    """consent 미활성 시 /onboarding/interests POST → 403."""
    access = await _signup_login(client, "gated@example.com")
    headers = {"Authorization": f"Bearer {access}"}

    resp = await client.post(
        "/onboarding/interests",
        headers=headers,
        json={"cso_cluster_ids": [], "user_class": "general"},
    )
    # consent gate 미들웨어가 차단 → 403 consent.required
    assert resp.status_code == 403
    assert resp.json()["code"] in (
        "consent.required",
        "onboarding.consent_required",
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_account_deletion_enqueue(client: AsyncClient) -> None:
    """account-deletion 은 202 + expected_deletion_by 반환 (worker async)."""
    access = await _signup_login(client, "delete-me@example.com")
    headers = {"Authorization": f"Bearer {access}"}
    # consent 등록 필요 없음 — account-deletion 은 personalization endpoint 가 아님
    resp = await client.post(
        "/consent/account-deletion",
        headers=headers,
        json={"confirmation": "confirm"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert "expected_deletion_by" in body
    assert body["request_id"]


# ============================================================
# A4 prep — 회귀 가드
# ============================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_consent_post_requires_agreed_true(client: AsyncClient) -> None:
    """agreed=false → 422 validation_error. 철회는 /consent/revoke 전용."""
    access = await _signup_login(client, "agreed-false@example.com")
    headers = {"Authorization": f"Bearer {access}"}

    resp = await client.post(
        "/consent",
        headers=headers,
        json={"consent_type": "personalization", "agreed": False},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "validation_error"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_consent_revoke_invalidates_recommendation_cache(
    client: AsyncClient, redis_client: aioredis.Redis
) -> None:
    """revoke 시 RedisKey.recommendation_cache(user_id) DEL — FR-59."""
    access = await _signup_login(client, "revoke-cache@example.com")
    headers = {"Authorization": f"Bearer {access}"}
    await _grant_consent(client, headers)

    me = await client.get("/auth/me", headers=headers)
    user_id = me.json()["user_id"]
    cache_key = f"recommendation:{user_id}"

    await redis_client.set(cache_key, "stale-payload", ex=3600)
    assert await redis_client.exists(cache_key) == 1

    resp = await client.post(
        "/consent/revoke",
        headers=headers,
        json={"consent_type": "personalization", "confirmation": "confirm"},
    )
    assert resp.status_code == 200, resp.text
    assert await redis_client.exists(cache_key) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_consent_gate_blocks_other_protected_endpoint(
    client: AsyncClient,
) -> None:
    """/topics/cso/clusters 도 ConsentGate PROTECTED_PATTERNS 적용 — 403 consent.required."""
    access = await _signup_login(client, "gate-other@example.com")
    headers = {"Authorization": f"Bearer {access}"}

    resp = await client.get("/topics/cso/clusters", headers=headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "consent.required"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_account_deletion_dedup_lock_persists(
    client: AsyncClient,
    redis_client: aioredis.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1차 202 + RedisKey.account_deletion_pending lock SET → 2차 시도는
    middleware deletion gate 가 401 deletion_in_progress 반환 (C-23)."""
    _patch_rq_enqueue(monkeypatch)

    access = await _signup_login(client, "deletion-dedup@example.com")
    headers = {"Authorization": f"Bearer {access}"}
    me = await client.get("/auth/me", headers=headers)
    user_id = me.json()["user_id"]

    resp = await client.post(
        "/consent/account-deletion",
        headers=headers,
        json={"confirmation": "confirm"},
    )
    assert resp.status_code == 202, resp.text
    lock_key = f"account_deletion:{user_id}"
    assert await redis_client.exists(lock_key) == 1

    resp = await client.post(
        "/consent/account-deletion",
        headers=headers,
        json={"confirmation": "confirm"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "consent.deletion_in_progress"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_account_deletion_revokes_all_refresh(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """deletion 후 기존 refresh 로 /auth/refresh → 401 refresh_revoked
    (revoke_all_user_refresh 가 meta active=0 → Lua replay 분기)."""
    _patch_rq_enqueue(monkeypatch)

    access, refresh = await _signup_login_tokens(
        client, "deletion-refresh@example.com"
    )
    headers = {"Authorization": f"Bearer {access}"}

    resp = await client.post(
        "/consent/account-deletion",
        headers=headers,
        json={"confirmation": "confirm"},
    )
    assert resp.status_code == 202, resp.text

    resp = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "auth.refresh_revoked"
