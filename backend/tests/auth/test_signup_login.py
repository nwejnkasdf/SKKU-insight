"""auth 통합 — signup → login → me chain (DB+Redis 필요).

email 정규화 3겹 검증: Test@TEST.com 으로 signup 후 test@test.com 으로 다시 시도 → 409.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.integration
async def test_signup_login_me_chain(client: AsyncClient) -> None:
    email = "Chain-Test@example.com"
    password = "Quick-Brown-Fox-2026"

    # 1) signup — 201
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": password}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["onboarding_required"] is True
    assert body["consent_required"] is True
    # email 정규화 확인 — 응답 email 은 lowercase
    assert body["email"] == email.lower()

    # 2) 같은 email 변형(대소문자) 으로 다시 signup → 409 (functional index)
    resp = await client.post(
        "/auth/signup",
        json={"email": email.upper(), "password": password},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "auth.email_taken"

    # 3) login — 200
    resp = await client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    access = tokens["access_token"]
    refresh = tokens["refresh_token"]
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 15 * 60

    # 4) me — 200
    resp = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["email"] == email.lower()
    assert me["consent_active"] is False
    assert me["onboarding_complete"] is False

    # 5) refresh — 200, 새 토큰
    resp = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200, resp.text
    new_tokens = resp.json()
    assert new_tokens["access_token"] != access
    assert new_tokens["refresh_token"] != refresh

    # 6) 이전 refresh 재사용 → 401 family revoke (HMAC :rotated 감지)
    resp = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "auth.refresh_revoked"

    # 7) family revoke 후 새 refresh 도 사용 불가
    resp = await client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert resp.status_code == 401, resp.text
