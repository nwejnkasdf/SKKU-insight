"""JWT encode/decode + HMAC compute 단위 테스트 (Redis 불필요)."""
from __future__ import annotations

from uuid import uuid4

import pytest
from jose import ExpiredSignatureError, JWTError

from app.contracts import TokenAudience
from app.security.jwt import compute_refresh_hmac, decode_access, encode_access


def test_encode_decode_roundtrip() -> None:
    user_id = uuid4()
    token, jti = encode_access(user_id, TokenAudience.USER)
    claims = decode_access(token, TokenAudience.USER)
    assert claims["sub"] == str(user_id)
    assert claims["aud"] == "user"
    assert claims["jti"] == jti


def test_decode_wrong_audience() -> None:
    user_id = uuid4()
    token, _ = encode_access(user_id, TokenAudience.USER)
    with pytest.raises(JWTError):
        decode_access(token, TokenAudience.ADMIN)


def test_admin_audience() -> None:
    admin_id = uuid4()
    token, _ = encode_access(admin_id, TokenAudience.ADMIN)
    claims = decode_access(token, TokenAudience.ADMIN)
    assert claims["aud"] == "admin"


def test_hmac_deterministic() -> None:
    """같은 token + JWT_SECRET 은 같은 HMAC."""
    token = "some-opaque-refresh-token-12345"
    h1 = compute_refresh_hmac(token)
    h2 = compute_refresh_hmac(token)
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex


def test_hmac_different_tokens() -> None:
    h1 = compute_refresh_hmac("token-a")
    h2 = compute_refresh_hmac("token-b")
    assert h1 != h2


def test_scope_claim_optional() -> None:
    user_id = uuid4()
    token1, _ = encode_access(user_id, TokenAudience.USER)
    token2, _ = encode_access(user_id, TokenAudience.USER, scope=["consent_active"])
    claims1 = decode_access(token1, TokenAudience.USER)
    claims2 = decode_access(token2, TokenAudience.USER)
    assert "scope" not in claims1
    assert claims2["scope"] == ["consent_active"]
