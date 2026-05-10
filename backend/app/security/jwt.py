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


# Redis Lua: verify + mark rotated + issue new (decision-backlog C-21, 옵션 B).
# C-7 (Lua CAS) 후속 강화 — 기존에는 verify+mark rotated 만 Lua atomic, issue_refresh
# (new meta HSET + new index SET) 가 Python 별도 호출이라 좁은 window 가 존재
# (T1 'ok' 후 issue_refresh 진행 중 T2 'replay' family revoke 가 T1 신규 meta 를
# 누락 가능). 본 Lua 가 verify·rotate·issue 를 단일 unit 으로 처리해 window 완전 제거.
#
# Python 은 new_token / new_jti / new_token_hmac 를 미리 생성해 KEYS/ARGV 로 전달.
#
# KEYS[1] = refresh_index:{HMAC(old_token)}       (old index)
# KEYS[2] = refresh:{user_id}:{old_jti}            (old meta)
# KEYS[3] = refresh:{user_id}:{new_jti}            (new meta — Lua 가 INSERT)
# KEYS[4] = refresh_index:{HMAC(new_token)}        (new index — Lua 가 SET)
# ARGV[1] = rotated_pointer_value                  ("uid:old_jti:rotated")
# ARGV[2] = new_active_pointer_value                ("uid:new_jti:active")
# ARGV[3] = ttl_seconds (str int)
# ARGV[4] = now_iso (created_at / last_used_at)
# ARGV[5] = ip
# ARGV[6] = ua_short
# ARGV[7] = old_jti (rotated_from)
#
# 반환:
#   {"ok",      pointer}  — verify+rotate+issue 완료 (new meta/index 이미 active)
#   {"replay",  pointer}  — 이미 rotated/revoked 또는 meta 비활성 (caller family revoke)
#   {"miss",    nil}      — index 없음 (caller 단순 401)
_LUA_VERIFY_ROTATE_ISSUE = """
local pointer = redis.call('GET', KEYS[1])
if not pointer then
    return {'miss', false}
end
local sep1 = string.find(pointer, ':', 1, true)
if not sep1 then return {'replay', pointer} end
local sep2 = string.find(pointer, ':', sep1 + 1, true)
if not sep2 then return {'replay', pointer} end
local state = string.sub(pointer, sep2 + 1)
if state ~= 'active' then
    return {'replay', pointer}
end
local active = redis.call('HGET', KEYS[2], 'active')
if active ~= '1' then
    return {'replay', pointer}
end
local ttl = tonumber(ARGV[3])
-- 1) old index → :rotated (기존 TTL 유지 — 단 GET 이후 다른 호출이 끼어들 가능성
--    무시 가능 — 본 Lua 자체가 atomic 이므로 GET TTL→SET 사이에 외부 변경 불가).
local old_ttl = redis.call('TTL', KEYS[1])
if old_ttl and old_ttl > 0 then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', old_ttl)
else
    redis.call('SET', KEYS[1], ARGV[1])
end
-- 2) old meta inactive (감사 로그)
redis.call('HSET', KEYS[2], 'active', '0')
-- 3) new meta INSERT — 모든 필드 한 번에
redis.call('HSET', KEYS[3],
    'created_at', ARGV[4],
    'last_used_at', ARGV[4],
    'ip', ARGV[5],
    'ua', ARGV[6],
    'rotated_from', ARGV[7],
    'active', '1'
)
redis.call('EXPIRE', KEYS[3], ttl)
-- 4) new active index
redis.call('SET', KEYS[4], ARGV[2], 'EX', ttl)
return {'ok', pointer}
"""


async def verify_refresh_and_rotate(
    refresh_token: str,
    *,
    ip: str,
    ua: str,
    redis: aioredis.Redis,
) -> tuple[str, str, UUID]:
    """refresh token 검증 + family revoke 감지 + 회전 + 새 token 발급 — atomic.

    옵션 B (decision-backlog C-21): verify · rotate · issue 를 Lua 단일 unit 으로 처리해
    C-7 의 잔여 race window (Lua CAS 후 Python issue_refresh 사이) 완전 제거.

    Python 은 new_token / new_jti / new_token_hmac 만 사전 생성. Lua 가 KEYS/ARGV 로
    받아 모든 Redis 변경 (old index :rotated, old meta inactive, new meta, new index)
    을 atomic 으로 수행.

    'miss'   → 단순 RefreshRevoked (user_id 역추적 불가)
    'replay' → 이미 회전·폐기 → user family revoke 후 RefreshRevoked
    'ok'     → 새 token 활성 (Lua 가 이미 INSERT). Python 은 token/jti 만 반환
    """
    settings = get_settings()
    ttl_seconds = settings.JWT_REFRESH_DAYS * 86_400

    # 1) 사전 GET — user_id/old_jti 추출 위해 1회 GET. Lua 안에서 다시 검증하므로 race 안전.
    old_token_hmac = compute_refresh_hmac(refresh_token)
    old_index_key = RedisKey.refresh_index(old_token_hmac)
    pointer = await redis.get(old_index_key)
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

    # 2) Python 에서 new token / jti / hmac 사전 생성 — Lua 의 KEYS/ARGV 로 전달
    new_token = secrets.token_urlsafe(64)
    new_jti = str(uuid4())
    new_token_hmac = compute_refresh_hmac(new_token)

    old_meta_key = RedisKey.refresh_token(user_id, old_jti)
    new_meta_key = RedisKey.refresh_token(user_id, new_jti)
    new_index_key = RedisKey.refresh_index(new_token_hmac)

    rotated_pointer = f"{user_id_str}:{old_jti}:rotated"
    new_active_pointer = f"{user_id_str}:{new_jti}:active"
    now_iso = _now_iso()

    # 3) Lua atomic: verify + rotate + issue 단일 unit
    result = await redis.eval(  # type: ignore[misc]
        _LUA_VERIFY_ROTATE_ISSUE,
        4,
        old_index_key,
        old_meta_key,
        new_meta_key,
        new_index_key,
        rotated_pointer,
        new_active_pointer,
        str(ttl_seconds),
        now_iso,
        ip,
        ua[:200],
        old_jti,
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
    # Lua 가 new meta + new index 이미 SET 완료 — Python 은 token/jti 반환만
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
