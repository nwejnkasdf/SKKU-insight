"""structlog 비밀값 마스킹 processor.

`password`, `password_hash`, `access_token`, `refresh_token`, `Authorization`,
`X-Idempotency-Key` 키가 로그에 들어가면 `***MASKED***` 로 치환.

structlog.configure 의 processors 체인에 추가.
"""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

MASK_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "access_token",
        "refresh_token",
        "authorization",
        "x-idempotency-key",
        "current_password",
        "new_password",
        "jwt_secret",
        "openai_api_key",
        "anthropic_api_key",
        "openrouter_api_key",
        "codex_oauth_token",
    }
)
MASK_VALUE = "***MASKED***"


def mask_secrets(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """structlog processor — event_dict 의 민감 키를 일괄 마스킹.

    structlog 의 Processor 시그니처: `(WrappedLogger, str, MutableMapping[str,Any])
    → Mapping[str,Any] | str | bytes | bytearray | tuple[...]`.
    """
    return _mask_dict(dict(event_dict))


def _mask_dict(d: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(k, str) and k.lower() in MASK_KEYS:
            result[k] = MASK_VALUE
        elif isinstance(v, dict):
            result[k] = _mask_dict(v)
        elif isinstance(v, list):
            result[k] = [_mask_dict(i) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


__all__ = ["MASK_KEYS", "MASK_VALUE", "mask_secrets"]
