"""JWT 인코드·디코드 + Refresh opaque token + HMAC :rotated family revoke 패턴.

핵심 설계 (token-handling.md):
- Access: HS256, 15분, aud="user"|"admin", jti UUID
- Refresh: opaque random 64바이트 (서명 없음). Redis 메타 `refresh:{user_id}:{jti}` hash + 인덱스 `refresh_index:{HMAC(token)}` string
- **Rotation 시 old index 삭제 X — 값을 `:rotated`로 OVERWRITE** → 재사용 감지 시 user_id 역추적 → family revoke (decision-backlog C-6)
- denylist: `jwt_denylist:{jti}` 잔여 15m TTL
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from jose import ExpiredSignatureError, JWTError, jwt

from app.config import get_settings
from app.contracts import ErrorCode, RedisKey, TokenAudience


class RefreshRevoked(Exception):
    """refresh 토큰 폐기됨. router 가 401 + AUTH_REFRESH_REVOKED 로 변환."""

    def __init__(self, reason: str = "revoked") -> None:
        self.reason = reason
        self.code = ErrorCode.AUTH_REFRESH_REVOKED
        super().__init__(f"refresh revoked: {reason}")


@dataclass(slots=True)
class RefreshContext:
    user_id: UUID
    jti: str


def encode_access(
    user_id: UUID,
    audience: TokenAudience,
    *,
    scope: list[str] | None = None,
) -> tuple[str, str]:
    """access JWT 발급. 반환: (token, jti). jti 는 denylist 키 + 로그 상관관계."""
    settings = get_settings()
    jti = str(uuid4())
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": settings.JWT_ISSUER,
        "sub": str(user_id),
        "aud": audience.value,
        "iat": now,
        "exp": now + settings.JWT_ACCESS_MINUTES * 60,
        "jti": jti,
    }
    if scope:
        payload["scope"] = scope
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
    return token, jti


def decode_access(
    token: str, expected_audience: TokenAudience
) -> dict[str, object]:
    """access JWT 검증. 실패 시 JWTError / ExpiredSignatureError raise."""
    settings = get_settings()
    payload: dict[str, object] = jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=["HS256"],
        audience=expected_audience.value,
        issuer=settings.JWT_ISSUER,
        options={"require": ["exp", "iat", "sub", "aud", "jti", "iss"]},
    )
    return payload


def compute_refresh_hmac(refresh_token: str) -> str:
    """opaque refresh token 의 HMAC-SHA256 인덱스 키 부분.

    HMAC-SHA256(JWT_SECRET, token) 의 hex digest. 같은 토큰 → 같은 HMAC.
    JWT_SECRET 회전 시 모든 index 무효화 (의도된 부수효과).
    """
    settings = get_settings()
    return hmac.new(
        settings.JWT_SECRET.encode("utf-8"),
        refresh_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def issue_refresh(
    user_id: UUID,
    *,
    ip: str,
    ua: str,
    redis: aioredis.Redis,
    rotated_from: str | None = None,
) -> tuple[str, str]:
    """새 refresh token + jti 발급. Redis meta hash + active index 둘 다 SET. 반환: (token, jti)."""
    settings = get_settings()
    token = secrets.token_urlsafe(64)
    jti = str(uuid4())
    ttl_seconds = settings.JWT_REFRESH_DAYS * 86_400
    meta_key = RedisKey.refresh_token(user_id, jti)
    index_key = RedisKey.refresh_index(compute_refresh_hmac(token))
    now_iso = _now_iso()
    await redis.hset(
        meta_key,
        mapping={
            "created_at": now_iso,
            "last_used_at": now_iso,
            "ip": ip,
            "ua": ua[:200],  # User-Agent 길이 제한
            "rotated_from": rotated_from or "",
            "active": "1",
        },
    )
    await redis.expire(meta_key, ttl_seconds)
    await redis.set(index_key, f"{user_id}:{jti}:active", ex=ttl_seconds)
    return token, jti


async def verify_refresh_and_rotate(
    refresh_token: str,
    *,
    ip: str,
    ua: str,
    redis: aioredis.Redis,
) -> tuple[str, str, UUID]:
    """refresh token 검증 + family revoke 감지 + 회전 + 새 (token, jti, user_id) 반환.

    HMAC index miss → 단순 RefreshRevoked.
    HMAC index 히트 + 값에 `:rotated`/`:revoked` → user family revoke 후 RefreshRevoked.
    HMAC index 히트 + `:active` + meta active="1" → 회전 (old index OVERWRITE to :rotated, old meta active="0").
    """
    index_key = RedisKey.refresh_index(compute_refresh_hmac(refresh_token))
    pointer = await redis.get(index_key)
    if pointer is None:
        raise RefreshRevoked("index_miss")
    pointer_str = pointer if isinstance(pointer, str) else pointer.decode()
    parts = pointer_str.split(":")
    if len(parts) != 3:
        raise RefreshRevoked("malformed_index")
    user_id_str, old_jti, state = parts
    user_id = UUID(user_id_str)
    if state in ("rotated", "revoked"):
        # 재사용 감지 — 탈취 의심 → family revoke
        await revoke_all_user_refresh(user_id, redis)
        raise RefreshRevoked("replay_detected")
    if state != "active":
        raise RefreshRevoked(f"unknown_state:{state}")
    meta_key = RedisKey.refresh_token(user_id, old_jti)
    meta = await redis.hgetall(meta_key)
    if not meta or _decode_field(meta, "active") != "1":
        # meta 와 index 불일치 (race 또는 부분 폐기) → family revoke
        await revoke_all_user_refresh(user_id, redis)
        raise RefreshRevoked("meta_inactive")
    # 회전:
    # 1) old index 값을 :rotated 로 OVERWRITE (TTL 그대로 KEEPTTL)
    await redis.set(
        index_key, f"{user_id}:{old_jti}:rotated", keepttl=True
    )
    # 2) old meta hash active="0" (감사 로그)
    await redis.hset(meta_key, "active", "0")
    # 3) 새 token + meta + active index
    new_token, new_jti = await issue_refresh(
        user_id, ip=ip, ua=ua, redis=redis, rotated_from=old_jti
    )
    return new_token, new_jti, user_id


async def revoke_refresh_jti(user_id: UUID, jti: str, redis: aioredis.Redis) -> None:
    """특정 jti 만 폐기 (로그아웃 — 다른 디바이스 세션 유지)."""
    meta_key = RedisKey.refresh_token(user_id, jti)
    await redis.hset(meta_key, "active", "0")
    # NOTE: index 는 SCAN 으로 찾기 어려우나 메타 active="0" 만으로도 verify 단계에서
    # family revoke 트리거됨. 명시 :revoked 마킹은 logout-all 등 일괄 경로에서만 사용.


async def revoke_all_user_refresh(user_id: UUID, redis: aioredis.Redis) -> None:
    """user namespace 의 모든 refresh meta hash 폐기. SCAN 으로 검색.

    `refresh_index:{HMAC}` 는 SCAN 으로 user 못 찾음 (단방향 HMAC) → TTL 14d 자연 만료에 위임.
    단 다음 verify 호출 시 meta active="0" 발견하므로 결과적으로 모두 차단됨.
    """
    pattern = f"refresh:{user_id}:*"
    async for key in redis.scan_iter(match=pattern):
        await redis.delete(key)


async def denylist_access(jti: str, *, ttl_seconds: int, redis: aioredis.Redis) -> None:
    """access JWT 즉시 폐기 (logout). TTL = 잔여 access exp 까지."""
    if ttl_seconds <= 0:
        return  # 이미 만료된 토큰 — denylist 불필요
    await redis.set(RedisKey.jwt_denylist(jti), "1", ex=ttl_seconds)


async def is_jti_denylisted(jti: str, redis: aioredis.Redis) -> bool:
    """미들웨어가 매 access 호출 시 체크."""
    return await redis.exists(RedisKey.jwt_denylist(jti)) > 0


# === 내부 헬퍼 ===

def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _decode_field(meta: object, key: str) -> str | None:
    """Redis hgetall 결과 (dict[str,str] or dict[bytes,bytes]) 에서 안전하게 추출."""
    if not isinstance(meta, dict):
        return None
    # decode_responses=True 사용 시 str dict, False 시 bytes dict — 둘 다 대응
    for k, v in meta.items():
        k_str = k if isinstance(k, str) else k.decode()
        if k_str == key:
            return v if isinstance(v, str) else v.decode()
    return None


__all__ = [
    "ExpiredSignatureError",
    "JWTError",
    "RefreshContext",
    "RefreshRevoked",
    "compute_refresh_hmac",
    "decode_access",
    "denylist_access",
    "encode_access",
    "is_jti_denylisted",
    "issue_refresh",
    "revoke_all_user_refresh",
    "revoke_refresh_jti",
    "verify_refresh_and_rotate",
]
