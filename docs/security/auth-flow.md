# 인증 흐름

본 파일은 SKKU InSight의 회원가입, 로그인, 토큰 갱신, 로그아웃, 동의 철회 플로우를 시퀀스 다이어그램으로 정의한다. 관련 FR: FR-01~04, FR-11. 관련 NFR: NFR-15~17, NFR-20. API 표는 [`../api/auth.md`](../api/auth.md), 토큰 처리는 [`token-handling.md`](token-handling.md), 비밀번호 정책은 [`password-policy.md`](password-policy.md).

## 결정 핀

- **bcrypt cost = 12** (passlib 기본값보다 높임 — NFR-16)
- **JWT Access**: 15분, HS256, 클레임 `{sub, aud, exp, iat, jti, iss}`
- **JWT Refresh**: opaque random 64바이트 token (서명 없음). Redis에 메타 저장 (`refresh:{user_id}:{jti} → {created_at, last_used_at, ip, ua}`)
- **Refresh 만료**: 14일
- **Electron 토큰 보관**: `safeStorage` (OS 키체인). 메모리에 평문 access만 유지

## 1. 회원가입

```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자
    participant E as Electron App
    participant API as FastAPI /auth
    participant Sec as security 모듈
    participant DB as Postgres

    U->>E: 이메일, 비밀번호 입력
    E->>E: 클라이언트 측 비밀번호 길이/복잡도 사전 검증
    E->>API: POST /auth/signup
    API->>Sec: enforce_password_policy(password)
    alt 정책 위반
        API-->>E: 422 + auth.weak_password
        E->>U: 정책 안내 표시
    end
    API->>Sec: bcrypt(cost=12) hash(password)
    Sec-->>API: password_hash
    API->>DB: INSERT User(email, password_hash) ON CONFLICT(email) RAISE
    alt email 중복
        API-->>E: 409 + auth.email_taken
    else 성공
        DB-->>API: user_id
        API-->>E: 201 + {user_id, onboarding_required:true, consent_required:true}
        E->>U: 동의 화면으로 이동
    end
```

가입만으로는 로그인 토큰 미발급. 별도 `/auth/login` 호출 필요.

## 2. 로그인

```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자
    participant E as Electron App
    participant API as FastAPI /auth
    participant Sec as security
    participant Redis as Redis (refresh store)
    participant DB as Postgres

    U->>E: 이메일, 비밀번호
    E->>API: POST /auth/login
    Note over API: rate limit 5/분/IP (slowapi)
    API->>DB: SELECT User WHERE email
    alt User 없음 또는 deleted_at != NULL
        API-->>E: 401 + auth.invalid_credentials
        Note over API: 의도적 동일 메시지 (Username Enumeration 방지)
    end
    API->>Sec: verify_password(input, password_hash)
    alt mismatch
        API-->>E: 401 + auth.invalid_credentials
    end
    API->>Sec: build_access_token(user_id, aud="user")
    API->>Sec: random_refresh_token() + jti
    API->>Redis: SET refresh:{user_id}:{jti} {created_at, ip, ua} EX 14d
    API->>DB: UPDATE User SET last_login_at=now()
    API-->>E: 200 + {access_token, refresh_token, expires_in:900, token_type:Bearer}
    E->>E: safeStorage에 access+refresh 저장
```

## 3. 토큰 갱신

```mermaid
sequenceDiagram
    autonumber
    participant E as Electron App
    participant API as FastAPI /auth
    participant Redis
    participant Sec

    E->>API: POST /auth/refresh {refresh_token}
    API->>API: extract_jti_from(refresh_token) -- 우리는 opaque이므로 token 자체가 키 일부
    API->>Redis: GET refresh:{user_id}:{jti}
    alt 없음 (폐기)
        API-->>E: 401 + auth.refresh_revoked
    end
    API->>Sec: verify_refresh_token(refresh_token, redis_meta)
    Sec-->>API: ok
    API->>Sec: rotate (issue new refresh + new jti)
    API->>Redis: DEL old + SET new refresh:{user_id}:{new_jti} EX 14d
    API->>Sec: build_access_token(user_id, aud)
    API-->>E: 200 + new {access_token, refresh_token}
```

refresh rotation: 매 갱신마다 새 jti. 도난된 토큰을 한 번 사용하면 정상 사용자도 끊겨서 즉시 인지.

## 4. 로그아웃

```mermaid
sequenceDiagram
    actor U as 사용자
    participant E
    participant API as FastAPI /auth
    participant Redis

    U->>E: 로그아웃 클릭
    E->>API: POST /auth/logout (Bearer access)
    API->>Redis: DEL refresh:{user_id}:{jti}   # 현재 jti만
    API->>Redis: SADD jwt_denylist:{jti} (TTL 15분)  # access 만료 시까지
    API-->>E: 200
    E->>E: safeStorage에서 토큰 삭제
```

전체 디바이스 로그아웃이 필요하면 `/auth/logout-all` (옵션) — `refresh:{user_id}:*` 패턴 일괄 삭제.

## 5. 동의 철회 → 인증과의 상호작용

```mermaid
sequenceDiagram
    actor U as 사용자
    participant E as Electron App
    participant API as FastAPI
    participant Redis
    participant DB

    U->>E: 설정 화면에서 동의 철회 클릭
    E->>API: POST /consent/revoke {consent_type:personalization, confirmation:confirm}
    API->>DB: UPDATE UserConsent SET revoked_at=now()
    API->>Redis: DEL recommendation:{user_id}    # 추천 캐시 폐기
    API-->>E: 200
    E->>E: 로컬 상태 갱신 -> consent_active=false
    Note over E: 토큰은 유지. 사용자가 재동의/삭제까지 다른 화면 노출 X
    E->>API: GET /recommendations/dashboard
    API->>API: check consent.active
    API-->>E: 403 + recommendation.consent_required
    E->>U: UI-05 변형 (재동의/계정삭제) 화면
```

## 인증 미들웨어 의사 코드

```python
async def auth_middleware(request, call_next):
    token = extract_bearer(request)
    if not token:
        return PlainTextResponse(status_code=401)
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], audience=expected_aud(request))
    except ExpiredSignatureError:
        raise HTTPException(401, code="auth.token_expired")
    except JWTError:
        raise HTTPException(401, code="auth.invalid_token")
    if await redis.sismember(RedisKey.jwt_denylist(payload["jti"]), "1"):
        # ErrorCode.AUTH_INVALID_TOKEN — 폐기된 토큰은 invalid_token 으로 흡수 (재로그인 필요).
        raise HTTPException(401, code="auth.invalid_token")
    request.state.user_id = payload["sub"]
    request.state.aud = payload["aud"]
    return await call_next(request)
```

`/auth/*`와 `/health`는 미들웨어 우회 화이트리스트.

## 안전 권장

- **로그에 비밀번호/토큰 절대 미기록** — structlog processor로 마스킹
- **에러 메시지 통일** — Username enumeration 방지: 가입 시 `email_taken`을 줄 수밖에 없는데, 로그인 실패는 항상 `invalid_credentials`
- **HTTPS 강제** — 프록시 헤더 (`X-Forwarded-Proto`)로 검증, 비TLS 요청은 308 redirect
