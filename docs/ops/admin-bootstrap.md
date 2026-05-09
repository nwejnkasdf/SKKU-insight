# 관리자 부트스트랩

본 파일은 첫 관리자 계정 생성과 강제 비밀번호 변경 절차를 정의한다. 결정 매트릭스 §2 "관리자 부트스트랩 = 환경변수 + CLI" 룰을 따른다. 관련 FR: FR-60. 관련 NFR: NFR-22.

## 절차

```mermaid
flowchart LR
    A[.env에 ADMIN_BOOTSTRAP_*] --> B[make create-admin]
    B --> C[python -m scripts.create_admin]
    C --> D[AdminUser INSERT]
    D --> E[must_change_password=true]
    E --> F[관리자 첫 로그인]
    F --> G[change-password 강제]
```

## 환경변수

| Var | 예시 |
|---|---|
| `ADMIN_BOOTSTRAP_EMAIL` | `admin@insight.test` |
| `ADMIN_BOOTSTRAP_PASSWORD` | `Admin-Bootstrap-2026!` |
| `ADMIN_BOOTSTRAP_ROLE` | `super` |

`ADMIN_BOOTSTRAP_PASSWORD`는 [`../security/password-policy.md`](../security/password-policy.md) 룰을 만족해야 한다.

## CLI 스크립트

`scripts/create_admin.py`:

```python
"""부트스트랩 또는 추가 관리자 생성.
사용:
    python -m scripts.create_admin                       # .env 의 ADMIN_BOOTSTRAP_* 사용
    python -m scripts.create_admin --email a@b.com --role operator   # 인자 우선
"""
import asyncio
import os
import sys
from passlib.hash import bcrypt
from app.db import async_session
from app.admin.models import AdminUser

async def main(email: str | None = None, password: str | None = None, role: str = "super"):
    email = email or os.environ.get("ADMIN_BOOTSTRAP_EMAIL")
    password = password or os.environ.get("ADMIN_BOOTSTRAP_PASSWORD")
    role = os.environ.get("ADMIN_BOOTSTRAP_ROLE", role)
    if not email or not password:
        print("missing ADMIN_BOOTSTRAP_EMAIL / ADMIN_BOOTSTRAP_PASSWORD", file=sys.stderr)
        sys.exit(2)
    if role not in {"super", "operator", "read_only"}:
        print(f"invalid role: {role}", file=sys.stderr); sys.exit(2)
    enforce_password_policy(password)   # ../security/password-policy.md
    async with async_session() as s:
        existing = await s.execute(select(AdminUser).where(AdminUser.email == email))
        if existing.scalar_one_or_none():
            print(f"AdminUser {email} already exists", file=sys.stderr)
            sys.exit(1)
        admin = AdminUser(
            email=email,
            password_hash=bcrypt.using(rounds=int(os.environ.get("BCRYPT_COST", 12))).hash(password),
            role=role,
            status="active",
            must_change_password=True,
        )
        s.add(admin)
        await s.commit()
        print(f"created admin {admin.admin_id} role={role}")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--email")
    p.add_argument("--password")
    p.add_argument("--role", default="super")
    args = p.parse_args()
    asyncio.run(main(args.email, args.password, args.role))
```

## Makefile 타깃

`Makefile`:

```makefile
create-admin:
	docker compose run --rm api python -m scripts.create_admin

create-operator:
	docker compose run --rm api python -m scripts.create_admin --role operator
```

## 첫 로그인 강제 비밀번호 변경

1. 관리자 콘솔 (Next.js, `/admin-console`)에서 `ADMIN_BOOTSTRAP_EMAIL` + `ADMIN_BOOTSTRAP_PASSWORD`로 로그인.
2. 응답에 `must_change_password=true`.
3. 클라이언트는 즉시 비밀번호 변경 모달 표시. 변경 전에는 모든 다른 화면 접근 차단.
4. `POST /admin/auth/change-password` 호출 → `must_change_password=false`로 갱신.

`POST /admin/auth/change-password` 본문:

```python
class ChangeAdminPasswordRequest(BaseModel):
    current_password: str
    new_password: str
```

`new_password`도 password-policy 검증.

## 검증 체크리스트

- [ ] `make create-admin` 실행 시 AdminUser 1행 INSERT
- [ ] 동일 이메일 재실행 시 실패 (exit 1)
- [ ] role 미지정 시 super 기본
- [ ] 첫 로그인 응답에 `must_change_password=true`
- [ ] 변경 전 다른 admin API 호출 시 409 + `code=admin.must_change_password`
- [ ] 변경 후 정상 작동

## 운영 시 주의

- `.env`의 `ADMIN_BOOTSTRAP_PASSWORD`는 **반드시 첫 변경 후 의미 없는 값으로 교체** (또는 삭제).
- 추가 관리자 생성은 super가 admin-console UI를 통해 수행 (또는 CLI 재사용).
- 시연 후 `docker compose down -v`로 데이터를 지우면 부트스트랩을 다시 해야 한다.
