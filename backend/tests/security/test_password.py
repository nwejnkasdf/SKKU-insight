"""password policy + bcrypt 단위 테스트."""
from __future__ import annotations

import pytest

from app.security.password import (
    PolicyViolation,
    enforce_password_policy,
    hash_password,
    verify_password,
)


def test_hash_and_verify_roundtrip() -> None:
    h = hash_password("CorrectHorse2026!Password")
    assert verify_password("CorrectHorse2026!Password", h)
    assert not verify_password("wrong-password-abc", h)


def test_policy_too_short() -> None:
    with pytest.raises(PolicyViolation) as exc:
        enforce_password_policy("short1!")
    assert exc.value.sub_code == "too_short"


def test_policy_too_long() -> None:
    with pytest.raises(PolicyViolation) as exc:
        enforce_password_policy("x" * 129)
    assert exc.value.sub_code == "too_long"


def test_policy_common() -> None:
    with pytest.raises(PolicyViolation) as exc:
        enforce_password_policy("password123456")
    assert exc.value.sub_code in ("common", "forbidden_term")


def test_policy_contains_email_local_part() -> None:
    with pytest.raises(PolicyViolation) as exc:
        enforce_password_policy("gywndALongPart2026", email="gywnd@example.com")
    assert exc.value.sub_code == "contains_user_info"


def test_policy_forbidden_term() -> None:
    with pytest.raises(PolicyViolation) as exc:
        enforce_password_policy("MyInsightDemo2026")
    assert exc.value.sub_code == "forbidden_term"


def test_policy_whitespace() -> None:
    with pytest.raises(PolicyViolation) as exc:
        enforce_password_policy(" StrongPassword2026 ")
    assert exc.value.sub_code == "whitespace"


def test_policy_pass() -> None:
    enforce_password_policy(
        "Quick-Brown-Fox-Jumps-Over-2026", email="user@example.com"
    )
