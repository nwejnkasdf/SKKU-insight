"""auth 통합 — signup → login → me chain (DB+Redis 필요).

email 정규화 3겹 검증: Test@TEST.com 으로 signup 후 test@test.com 으로 다시 시도 → 409.

A4 prep — 누락된 회귀 가드 (weak_password rules / username enumeration / logout
denylist + refresh revoke / account deletion gate). PASS-TO-PASS 그물.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

_STRONG_PASSWORD = "Strong-Test-Password-2026"


async def _signup_login(
    client: AsyncClient, email: str, *, password: str = _STRONG_PASSWORD
) -> tuple[str, str]:
    """signup + login → (access, refresh)."""
    await client.post("/auth/signup", json={"email": email, "password": password})
    resp = await client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    tokens = resp.json()
    return tokens["access_token"], tokens["refresh_token"]


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


# ============================================================
# A4 prep — weak_password 정책 5 룰 회귀 가드
# ============================================================


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    "email,password,sub_code",
    [
        ("rule-short@example.com", "short", "too_short"),
        ("rule-whitespace@example.com", " WhitespacePadded2026 ", "whitespace"),
        ("rule-long@example.com", "a" * 129, "too_long"),
        (
            "verylongusername@example.com",
            "verylongusername-pass-2026",
            "contains_user_info",
        ),
        ("rule-forbidden@example.com", "InsightStrong-2026!!", "forbidden_term"),
    ],
)
async def test_signup_weak_password_rejected(
    client: AsyncClient, email: str, password: str, sub_code: str
) -> None:
    """enforce_password_policy 5 룰 각각 422 + sub_code 매칭."""
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": password}
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "auth.weak_password"
    assert body["details"]["sub_code"] == sub_code


@pytest.mark.asyncio
@pytest.mark.integration
async def test_signup_missing_email_422(client: AsyncClient) -> None:
    """Pydantic EmailStr 위반 → 422 validation_error (auth.weak_password 가 아님)."""
    resp = await client.post(
        "/auth/signup",
        json={"email": "not-an-email", "password": _STRONG_PASSWORD},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_login_unknown_email_invalid_credentials(
    client: AsyncClient,
) -> None:
    """Username enumeration 방지 — 미존재 user 도 invalid_credentials 동일 메시지."""
    resp = await client.post(
        "/auth/login",
        json={
            "email": "ghost-unknown@example.com",
            "password": _STRONG_PASSWORD,
        },
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "auth.invalid_credentials"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_logout_access_denylisted(client: AsyncClient) -> None:
    """logout 후 같은 access 재사용 → 401 (jwt_denylist:{jti} hit)."""
    access, _ = await _signup_login(client, "logout-denylist@example.com")
    headers = {"Authorization": f"Bearer {access}"}

    resp = await client.post("/auth/logout", headers=headers)
    assert resp.status_code == 204, resp.text

    resp = await client.get("/auth/me", headers=headers)
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "auth.invalid_token"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_logout_revokes_refresh_body(client: AsyncClient) -> None:
    """logout body 의 refresh token → 다음 refresh 시 401 refresh_revoked."""
    access, refresh = await _signup_login(client, "logout-refresh@example.com")
    headers = {"Authorization": f"Bearer {access}"}

    resp = await client.post(
        "/auth/logout", headers=headers, json={"refresh_token": refresh}
    )
    assert resp.status_code == 204, resp.text

    resp = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "auth.refresh_revoked"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_account_deletion_blocks_access(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/consent/account-deletion 후 같은 access → 401 deletion_in_progress
    (RedisKey.account_deletion_pending lock 가 middleware gate 트리거).

    RQ enqueue 는 real worker 의존이므로 no-op patch.
    """
    from app.consent import service as consent_service

    def _noop_enqueue(**kwargs: object) -> object:
        return None

    monkeypatch.setattr(
        consent_service, "_enqueue_account_deletion", _noop_enqueue
    )

    access, _ = await _signup_login(client, "delete-block@example.com")
    headers = {"Authorization": f"Bearer {access}"}

    resp = await client.post(
        "/consent/account-deletion",
        headers=headers,
        json={"confirmation": "confirm"},
    )
    assert resp.status_code == 202, resp.text

    resp = await client.get("/auth/me", headers=headers)
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "consent.deletion_in_progress"
