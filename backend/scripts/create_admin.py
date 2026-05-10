"""create_admin CLI — AdminUser 부트스트랩 1행 INSERT.

`make create-admin` 또는 `python -m scripts.create_admin`. env ADMIN_BOOTSTRAP_*
또는 argparse override 로 값 결정. must_change_password=True 항상.

docs/ops/admin-bootstrap.md.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.contracts import AdminRole
from app.db.models import AdminUser
from app.db.session import AsyncSessionLocal
from app.security.password import (
    PolicyViolation,
    enforce_password_policy,
    hash_password,
)


async def _create(email: str, password: str, role: str) -> int:
    email_normalized = email.strip().lower()
    if role not in {r.value for r in AdminRole}:
        print(f"[FAIL] role 은 {[r.value for r in AdminRole]} 중 하나여야 합니다.")
        return 1
    try:
        enforce_password_policy(password, email=email_normalized)
    except PolicyViolation as exc:
        print(f"[FAIL] 비밀번호 정책 위반: {exc.sub_code} — {exc.message}")
        return 1

    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(AdminUser).where(func.lower(AdminUser.email) == email_normalized)
        )
        if existing.scalars().first() is not None:
            print(
                f"[FAIL] 이미 존재하는 관리자 email={email_normalized}. "
                "삭제 후 재실행하거나 다른 email 사용."
            )
            return 1
        admin = AdminUser(
            email=email_normalized,
            password_hash=hash_password(password),
            role=role,
            status="active",
            must_change_password=True,
            created_at=datetime.now(UTC),
        )
        session.add(admin)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            print(f"[FAIL] DB 오류: {exc}")
            return 1
        await session.refresh(admin)
        print(
            f"[OK] AdminUser 생성 admin_id={admin.admin_id} "
            f"email={admin.email} role={admin.role} must_change_password=True"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="AdminUser 부트스트랩 INSERT")
    parser.add_argument("--email", default=settings.ADMIN_BOOTSTRAP_EMAIL)
    parser.add_argument("--password", default=settings.ADMIN_BOOTSTRAP_PASSWORD)
    parser.add_argument(
        "--role",
        default=settings.ADMIN_BOOTSTRAP_ROLE.value,
        choices=[r.value for r in AdminRole],
    )
    args = parser.parse_args(argv)
    return asyncio.run(_create(args.email, args.password, args.role))


if __name__ == "__main__":
    sys.exit(main())
