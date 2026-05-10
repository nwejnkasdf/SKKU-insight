"""Security 모듈 — password / JWT / rate-limit / consent cache / idempotency / Depends.

각 서브모듈 책임:
- password.py: bcrypt hash/verify + policy 검증 (deny-list 5,000 + 5 룰)
- jwt.py: access JWT 발급/검증 + refresh opaque token + HMAC :rotated family revoke
- rate_limit.py: slowapi Limiter + ErrorResponse 핸들러
- consent_cache.py: is_consent_active 60s Redis 캐시
- idempotency.py: X-Idempotency-Key Depends
- deps.py: get_current_user / get_current_admin / require_consent_active

연관 docs: security/auth-flow.md, token-handling.md, password-policy.md, rate-limiting.md.
"""
from __future__ import annotations

__all__: list[str] = []
