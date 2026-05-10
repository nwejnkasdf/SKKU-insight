"""bcrypt 해시 + 비밀번호 정책 검증.

정책 5 룰 (password-policy.md):
1. 길이 12-128 자
2. deny-list (`common_passwords.txt`) 중복 차단
3. email local part (≥4자) 포함 차단
4. 금칙어 `{insight, skku, admin, password, qwerty}` 포함 차단
5. 양끝 whitespace 차단

bcrypt cost = `settings.BCRYPT_COST` (default 12).

passlib 미사용 — bcrypt 4.x 와 passlib 1.7 호환성 깨짐 (bcrypt 가 `__about__` 제거).
직접 `bcrypt` 라이브러리 호출. UTF-8 한국어 + 128자 정책 모두 지원하기 위해
SHA-256 hex pre-hash (64 ASCII bytes — bcrypt 72 byte 한도 통과 + null byte 없음).
보안적으로 동등 — sha256 충돌 확률 무시 가능.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import bcrypt

from app.config import get_settings
from app.contracts import ErrorCode

_DENY_LIST_PATH = Path(__file__).parent / "common_passwords.txt"
_FORBIDDEN_TERMS = frozenset({"insight", "skku", "admin", "password", "qwerty"})

_settings = get_settings()
_deny_list: frozenset[str] = frozenset(
    line.strip().lower()
    for line in _DENY_LIST_PATH.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.strip().startswith("#")
)


class PolicyViolation(Exception):
    """비밀번호 정책 위반. router 가 422 + ErrorResponse 로 변환."""

    def __init__(self, sub_code: str, message: str) -> None:
        self.sub_code = sub_code
        self.message = message
        self.code = ErrorCode.AUTH_WEAK_PASSWORD
        super().__init__(message)


def _prep(password: str) -> bytes:
    """bcrypt 입력 정규화 — SHA-256 hex digest (64 ASCII bytes).

    이유:
    - bcrypt 4.x 는 72 byte 초과 입력을 ValueError 로 거부 (이전 버전은 silent truncate)
    - 정책은 최대 128자 허용 + UTF-8 한국어 1자=3byte → 최대 384 byte
    - SHA-256 hex 는 64 ASCII bytes — 한도 통과 + null byte 없음 + 충돌 무시 가능
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")


def hash_password(plain: str) -> str:
    """bcrypt 해시 (cost=BCRYPT_COST). 결과 길이 ~60자 ASCII."""
    salt = bcrypt.gensalt(rounds=_settings.BCRYPT_COST)
    return bcrypt.hashpw(_prep(plain), salt).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """bcrypt verify. False 시 invalid_credentials. timing attack 안전."""
    try:
        return bcrypt.checkpw(_prep(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
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
