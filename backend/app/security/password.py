"""bcrypt 해시 + 비밀번호 정책 검증.

정책 5 룰 (password-policy.md):
1. 길이 12-128 자
2. deny-list (`common_passwords.txt`) 중복 차단
3. email local part (≥4자) 포함 차단
4. 금칙어 `{insight, skku, admin, password, qwerty}` 포함 차단
5. 양끝 whitespace 차단

bcrypt cost = `settings.BCRYPT_COST` (default 12, passlib log_rounds).
"""
from __future__ import annotations

from pathlib import Path

from passlib.context import CryptContext

from app.config import get_settings
from app.contracts import ErrorCode

_DENY_LIST_PATH = Path(__file__).parent / "common_passwords.txt"
_FORBIDDEN_TERMS = frozenset({"insight", "skku", "admin", "password", "qwerty"})

_settings = get_settings()
_pwd_context = CryptContext(
    schemes=["bcrypt"],
    bcrypt__rounds=_settings.BCRYPT_COST,
    deprecated="auto",
)
_deny_list: frozenset[str] = frozenset(
    line.strip().lower()
    for line in _DENY_LIST_PATH.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.strip().startswith("#")
)


class PolicyViolation(Exception):
    """비밀번호 정책 위반. router 가 422 + ErrorResponse 로 변환."""

    def __init__(self, sub_code: str, message: str) -> None:
        self.sub_code = sub_code  # too_short / too_long / common / contains_user_info / whitespace / forbidden_term
        self.message = message
        self.code = ErrorCode.AUTH_WEAK_PASSWORD
        super().__init__(message)


def hash_password(plain: str) -> str:
    """bcrypt 해시 (cost=BCRYPT_COST). 결과 길이 ~60 자."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """bcrypt verify. False 시 invalid_credentials. timing attack 안전."""
    try:
        return _pwd_context.verify(plain, hashed)
    except ValueError:
        # 해시 형식 오류 — 마이그레이션 실패 등. 보수적으로 False.
        return False


def enforce_password_policy(password: str, *, email: str | None = None) -> None:
    """5 룰 검증. 통과 시 None, 위반 시 PolicyViolation raise."""
    if password.strip() != password:
        raise PolicyViolation(
            "whitespace", "비밀번호 양끝에 공백이 있습니다."
        )
    if len(password) < 12:
        raise PolicyViolation("too_short", "비밀번호는 최소 12자 이상이어야 합니다.")
    if len(password) > 128:
        raise PolicyViolation("too_long", "비밀번호는 최대 128자까지입니다.")
    lowered = password.lower()
    if lowered in _deny_list:
        raise PolicyViolation(
            "common", "흔한 비밀번호 목록에 포함된 비밀번호입니다."
        )
    if email:
        local_part = email.split("@", 1)[0].lower()
        if len(local_part) >= 4 and local_part in lowered:
            raise PolicyViolation(
                "contains_user_info",
                "비밀번호에 이메일 일부를 포함할 수 없습니다.",
            )
    for term in _FORBIDDEN_TERMS:
        if term in lowered:
            raise PolicyViolation(
                "forbidden_term",
                f"비밀번호에 '{term}' 같은 금칙어를 포함할 수 없습니다.",
            )


__all__ = [
    "PolicyViolation",
    "enforce_password_policy",
    "hash_password",
    "verify_password",
]
