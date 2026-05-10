"""/admin/users email 마스킹 룰 단위 테스트 (decision-backlog C-9).

규칙:
- super → 원문 그대로
- operator / read_only:
    - local part ≥ 2 자: 첫·마지막 글자 + `***`@domain
    - local part = 1 자: `***@domain` (전체 fallback)
"""
from __future__ import annotations

import pytest

from app.admin.users_service import mask_email


@pytest.mark.parametrize(
    "email,role,expected",
    [
        ("gywnd123@gmail.com", "super", "gywnd123@gmail.com"),
        ("gywnd123@gmail.com", "operator", "g***3@gmail.com"),
        ("gywnd123@gmail.com", "read_only", "g***3@gmail.com"),
        ("ab@example.com", "operator", "a***b@example.com"),
        ("a@example.com", "operator", "***@example.com"),
        ("a@example.com", "read_only", "***@example.com"),
        ("noatchar", "operator", "noatchar"),  # @ 없으면 원문
    ],
)
def test_mask_email(email: str, role: str, expected: str) -> None:
    assert mask_email(email, role) == expected
