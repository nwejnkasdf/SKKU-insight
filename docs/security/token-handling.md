# 토큰 처리

본 파일은 JWT Access 토큰의 클레임 구조와 Redis Refresh 토큰 메타 키 디자인을 정의한다. 관련 NFR: NFR-15, NFR-17, NFR-22. 흐름은 [`auth-flow.md`](auth-flow.md).

## JWT Access 토큰

### 알고리즘
- HS256 (대칭, 단일 백엔드 클러스터). 환경변수 `JWT_SECRET`. 64+ 자 랜덤.
- 향후 다중 서비스로 분리 시 RS256/EdDSA로 전환.

### 클레임

```json
{
  "iss": "skku-insight",
  "sub": "user_id_uuid_string",
  "aud": "user" | "admin",
  "iat": 1717000000,
  "exp": 1717000900,            // iat + 15분
  "jti": "uuid4",
  "scope": ["consent_active"]   // 선택적 부가 정보 (예: consent_active true일 때만 추가)
}
```

| 클레임 | 의미 |
|---|---|
| `iss` | 발급자. 환경변수 `JWT_ISSUER`. 검증 시 일치 강제 |
| `sub` | 주체. User.user_id 또는 AdminUser.admin_id |
| `aud` | 청자. `user`(일반) 또는 `admin`(관리자). 권한 분리 (FR-60) |
| `iat` | 발급 시각 |
| `exp` | 만료 시각. 15분 |
| `jti` | 토큰 고유 ID. denylist 키 |
| `scope` | 가드 정보. `consent_active`가 없으면 personalization API 차단 |

### 검증 체크리스트

```python
def verify_access(token: str, expected_aud: str):
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        audience=expected_aud,             # FR-60: aud 강제
        issuer=settings.jwt_issuer,
        options={"require": ["exp","iat","sub","aud","jti"]}
    )
    if redis.exists(f"jwt_denylist:{payload['jti']}"):
        raise TokenRevoked()
    return payload
```

`expected_aud`는 라우트에 따라 결정. `/admin/*`은 `admin`, 그 외는 `user`.

## Refresh 토큰

### 형태
- **opaque** random 64바이트 (base64url 인코딩 ≈ 86자 문자열)
- 서명 없음. Redis 메타가 진실의 출처.

### Redis 키 디자인

```
KEY: refresh:{user_id}:{jti}
TYPE: hash
TTL: 14d (rotate마다 갱신)

FIELDS:
  - created_at: ISO8601
  - last_used_at: ISO8601
  - ip: string (last refresh issuer's IP)
  - ua: string (User-Agent abbreviated)
  - rotated_from: previous_jti | null
  - active: "1" (활성) | "0" (회전/명시 폐기됨)
```

### 인덱스 키 (HMAC `:rotated` 마커 패턴 — A2 결정 2026-05-11)

refresh_token 자체는 opaque이므로 메인 메타 키를 빠르게 찾기 위한 인덱스가 필요. **회전 시 old index를 삭제하지 않고 값을 `:rotated`로 OVERWRITE**해 user_id 역추적 + family revoke 가능하게 함.

```
KEY: refresh_index:{HMAC_SHA256(JWT_SECRET, refresh_token)}
VALUE: "{user_id}:{jti}:active"   # 정상
       | "{user_id}:{jti}:rotated" # 이미 회전된 토큰 (재사용 시 family revoke 트리거)
       | "{user_id}:{jti}:revoked" # 명시 폐기 (logout, family revoke 등)
TTL: 14d (rotate 시 새 active index도 14d, old index는 TTL 유지)
```

검증 + replay 감지:

```python
async def verify_refresh(refresh_token: str, redis) -> RefreshContext:
    idx_key = f"refresh_index:{hmac_sha256(settings.jwt_secret, refresh_token)}"
    pointer = await redis.get(idx_key)
    if not pointer:
        # HMAC index 미스 → 토큰이 14d 초과 만료됐거나 완전 위조.
        # user_id 역추적 불가 → 단순 RefreshRevoked.
        raise RefreshRevoked(reason="index_miss")
    user_id, jti, state = pointer.split(":")
    if state in ("rotated", "revoked"):
        # 이미 회전됐거나 명시 폐기된 토큰이 다시 들어옴 = 탈취 의심
        # → user_id 추출 가능 → family revoke (모든 refresh 폐기)
        await revoke_all_user_refresh(user_id, redis)
        raise RefreshRevoked(reason="replay_detected")
    # state == "active"
    meta = await redis.hgetall(f"refresh:{user_id}:{jti}")
    if not meta or meta.get("active") != "1":
        # 메타와 index가 일관되지 않음 (race 또는 부분 폐기) → family revoke
        await revoke_all_user_refresh(user_id, redis)
        raise RefreshRevoked(reason="meta_inactive")
    return RefreshContext(user_id=user_id, jti=jti, meta=meta)
```

### 회전 (Rotation)

```python
async def rotate_refresh(ctx: RefreshContext, old_token: str, redis):
    new_token = secrets.token_urlsafe(64)
    new_jti = uuid4()
    # 1) old index 값을 :rotated 로 OVERWRITE (삭제 X — TTL 유지). 이후 재사용 시 replay 감지.
    old_idx = f"refresh_index:{hmac_sha256(secret, old_token)}"
    await redis.set(old_idx, f"{ctx.user_id}:{ctx.jti}:rotated", keepttl=True)
    # 2) old meta hash 도 active="0" 으로 마킹 (TTL 그대로, 감사 추적용)
    await redis.hset(f"refresh:{ctx.user_id}:{ctx.jti}", "active", "0")
    # 3) 새 meta hash + 새 active index 생성
    await redis.hset(f"refresh:{ctx.user_id}:{new_jti}", mapping={
        "created_at": now_iso(), "last_used_at": now_iso(),
        "ip": request.ip, "ua": request.ua_short(),
        "rotated_from": str(ctx.jti), "active": "1",
    })
    await redis.expire(f"refresh:{ctx.user_id}:{new_jti}", 14 * 86400)
    await redis.set(
        f"refresh_index:{hmac_sha256(secret, new_token)}",
        f"{ctx.user_id}:{new_jti}:active",
        ex=14 * 86400,
    )
    return new_token, new_jti


async def revoke_all_user_refresh(user_id: str, redis):
    """user namespace의 모든 refresh hash 폐기. index는 TTL 자연 만료 (SCAN으로 못 찾음)."""
    async for key in redis.scan_iter(match=f"refresh:{user_id}:*"):
        await redis.delete(key)
```

**핵심:** opaque token에서 user_id 직접 추출 불가. HMAC index가 유일한 역추적 경로이므로 회전 시 삭제 대신 `:rotated` 마킹으로 보존. 동일 user의 정상 refresh family는 별도 active index/메타로 관리되므로 영향 0.

## Access 토큰 denylist

로그아웃 시 access의 `jti`를 SET 추가. TTL = 남은 만료 시간. 미들웨어가 매 호출 때 체크.

```
KEY: jwt_denylist:{jti}
TYPE: string
VALUE: "1"
TTL: max(0, exp - now())
```

## Electron 클라이언트 보관

- `safeStorage.encryptString(token)` → DB에 저장 (electron-store + 암호화 결과)
- 평문은 메모리만
- 앱 재시작 시 `safeStorage.decryptString()`으로 복호화
- 키체인 우회 불가능 → 기기 잠금 해제 시에만 접근

## 시간 동기화

- 서버 NTP 필수 (Docker host)
- 클라이언트 시계 어긋나면 토큰 만료 인식 잘못될 수 있음 — 서버 응답에 `server_time` 헤더 포함, 클라이언트가 drift 감지 시 재로그인 권유

## 보안 체크리스트

- [ ] `JWT_SECRET` 64+ 문자 무작위
- [ ] 환경변수에서만 시크릿 주입, 코드/리포지토리 미포함
- [ ] aud 검증 (FR-60: 일반 토큰으로 admin API 차단)
- [ ] denylist + refresh rotation 양쪽
- [ ] HTTPS 강제 (NFR-20)
- [ ] 로그에 토큰 미기록 (structlog mask)
