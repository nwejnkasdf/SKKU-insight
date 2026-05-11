"""consent 통합 — GET / POST / revoke 흐름 + onboarding 게이팅."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _signup_login(client: AsyncClient, email: str) -> str:
    password = "Strong-Test-Password-2026"
    await client.post("/auth/signup", json={"email": email, "password": password})
    resp = await client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    return resp.json()["access_token"]


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
