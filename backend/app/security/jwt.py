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


# Redis Lua: index 검증 + 상태 전이를 atomic CAS 로 (codex review 2026-05-11 C-7).
# 동시 두 refresh 요청이 같은 active pointer 를 동시에 읽어 둘 다 새 token 을 발급
# 하는 race 차단. Lua 스크립트는 Redis 가 단일 thread 로 실행 → atomic.
#
# KEYS[1] = refresh_index:{HMAC(token)}
# KEYS[2] = refresh:{user_id}:{old_jti}  (meta hash)
# ARGV[1] = rotated_pointer_value  ("{user_id}:{old_jti}:rotated")
#
# 반환:
#   {"ok",      pointer}  — atomic transition 성공 (caller 가 새 token 발급)
#   {"replay",  pointer}  — 이미 rotated/revoked 또는 meta 비활성 (caller 가 family revoke)
#   {"miss",    nil}      — index 없음 (caller 가 단순 401)
_LUA_VERIFY_AND_MARK_ROTATED = """
local pointer = redis.call('GET', KEYS[1])
if not pointer then
    return {'miss', false}
end
local sep1, sep2 = string.find(pointer, ':', 1, true), nil
if not sep1 then return {'replay', pointer} end
sep2 = string.find(pointer, ':', sep1 + 1, true)
if not sep2 then return {'replay', pointer} end
local state = string.sub(pointer, sep2 + 1)
if state ~= 'active' then
    return {'replay', pointer}
end
local active = redis.call('HGET', KEYS[2], 'active')
if active ~= '1' then
    return {'replay', pointer}
end
-- atomic transition: index → :rotated (TTL 유지) + meta active='0'
local ttl = redis.call('TTL', KEYS[1])
if ttl and ttl > 0 then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ttl)
else
    redis.call('SET', KEYS[1], ARGV[1])
end
redis.call('HSET', KEYS[2], 'active', '0')
return {'ok', pointer}
"""


async def verify_refresh_and_rotate(
    refresh_token: str,
    *,
    ip: str,
    ua: str,
    redis: aioredis.Redis,
) -> tuple[str, str, UUID]:
    """refresh token 검증 + family revoke 감지 + 회전 + 새 (token, jti, user_id) 반환.

    Lua CAS (codex C-7): index 의 'active' 상태 확인 + ':rotated' 전이 + meta active='0'
    셋팅을 atomic 단일 연산으로. 동시 race 시 한 호출만 'ok', 나머지는 'replay'.

    'miss'   → 단순 RefreshRevoked (user_id 역추적 불가)
    'replay' → 이미 회전·폐기됨 → user family revoke 후 RefreshRevoked
    'ok'     → 새 token 발급 (issue_refresh)
    """
    token_hmac = compute_refresh_hmac(refresh_token)
    # 'placeholder' — pointer 를 모르므로 Lua 가 받은 pointer 그대로 :rotated 치환.
    # 즉 ARGV[1] 은 Lua 내부에서 동적 substitute (실제 코드는 Lua 가 pointer 의
    # 마지막 segment 만 ':rotated' 로 swap 하면 더 정확하지만, pointer format 이
    # `{uid}:{jti}:{state}` 로 고정이므로 ARGV[1] = pointer_without_state + ':rotated' 형태로
    # caller 가 만들어 전달하는 게 더 단순. 그러나 caller 는 pointer 모르므로 일단
    # ARGV[1] 을 빈 sentinel 로 두고 Lua 가 pointer 에서 추출).
    # → 더 단순: 호출 전에 Python 이 GET 으로 pointer 를 한 번 본 뒤 ARGV 만들고
    # Lua 가 GETSET 으로 atomic swap. 단 그 사이에 다른 worker 가 끼어들 수 있으므로
    # Lua 안에서 처리하는 게 더 안전. 그래서 ARGV 는 사용하지 않고 Lua 내부에서
    # pointer 의 ':active' suffix 만 ':rotated' 로 swap 한다.
    index_key = RedisKey.refresh_index(token_hmac)
    # 우선 user_id/jti 만 추출하기 위해 1회 GET (전이는 Lua atomic).
    pointer = await redis.get(index_key)
    if pointer is None:
        raise RefreshRevoked("index_miss")
    pointer_str = pointer if isinstance(pointer, str) else pointer.decode()
    parts = pointer_str.split(":")
    if len(parts) != 3:
        raise RefreshRevoked("malformed_index")
    user_id_str, old_jti, _state_hint = parts
    try:
        user_id = UUID(user_id_str)
    except ValueError as exc:
        raise RefreshRevoked("malformed_index") from exc
    meta_key = RedisKey.refresh_token(user_id, old_jti)
    rotated_pointer = f"{user_id_str}:{old_jti}:rotated"
    result = await redis.eval(  # type: ignore[misc]
        _LUA_VERIFY_AND_MARK_ROTATED,
        2,
        index_key,
        meta_key,
        rotated_pointer,
    )
    status = _redis_str(result[0]) if result else None
    if status == "miss":
        raise RefreshRevoked("index_miss")
    if status == "replay":
        await revoke_all_user_refresh(user_id, redis)
        raise RefreshRevoked("replay_detected")
    if status != "ok":
        await revoke_all_user_refresh(user_id, redis)
        raise RefreshRevoked(f"unknown_lua_status:{status}")
    # 새 token + meta + active index
    new_token, new_jti = await issue_refresh(
        user_id, ip=ip, ua=ua, redis=redis, rotated_from=old_jti
    )
    return new_token, new_jti, user_id


def _redis_str(value: object) -> str | None:
    """Lua 반환의 첫 element 가 bytes 또는 str — 정규화."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return str(value)


async def revoke_refresh_jti(user_id: UUID, jti: str, redis: aioredis.Redis) -> None:
    """특정 jti 만 폐기 (로그아웃 — 다른 디바이스 세션 유지)."""
    meta_key = RedisKey.refresh_token(user_id, jti)
    await redis.hset(meta_key, "active", "0")
    # NOTE: index 는 SCAN 으로 찾기 어려우나 메타 active="0" 만으로도 verify 단계에서
    # family revoke 트리거됨. 명시 :revoked 마킹은 logout-all 등 일괄 경로에서만 사용.


async def revoke_refresh_by_token(refresh_token: str, redis: aioredis.Redis) -> None:
    """로그아웃 시 client 가 전달한 refresh token 을 직접 폐기 (codex C-2).

    HMAC index 가 있으면 user_id/jti 추출 → meta active='0' + index 값을 ':revoked' 로
    OVERWRITE (TTL 유지). 다음 refresh 시도 시 family revoke 트리거.
    index 없으면 (만료/위조) silent no-op — 호출자는 access denylist 만으로 충분.
    """
    index_key = RedisKey.refresh_index(compute_refresh_hmac(refresh_token))
    pointer = await redis.get(index_key)
    if pointer is None:
        return
    pointer_str = pointer if isinstance(pointer, str) else pointer.decode()
    parts = pointer_str.split(":")
    if len(parts) != 3:
        return
    user_id_str, jti, _state = parts
    try:
        user_id = UUID(user_id_str)
    except ValueError:
        return
    meta_key = RedisKey.refresh_token(user_id, jti)
    await redis.set(index_key, f"{user_id_str}:{jti}:revoked", keepttl=True)
    await redis.hset(meta_key, "active", "0")


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
    "revoke_refresh_by_token",
    "revoke_refresh_jti",
    "verify_refresh_and_rotate",
]
