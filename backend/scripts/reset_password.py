"""reset_password CLI — 사용자 비밀번호 강제 변경 (decision-backlog P2-5).

사용: `python -m scripts.reset_password --email user@x --new-password new`.
시연 운영자 전용 (사용자가 비번 잊었을 때 임시 발급). policy 검증 + bcrypt 해시 +
모든 refresh family revoke.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import redis.asyncio as aioredis
from sqlalchemy import func, select, update

from app.config import get_settings
from app.db.models import User
from app.db.session import AsyncSessionLocal
from app.security.jwt import revoke_all_user_refresh
from app.security.password import (
    PolicyViolation,
    enforce_password_policy,
    hash_password,
)


async def _reset(email: str, new_password: str) -> int:
    email_normalized = email.strip().lower()
    try:
        enforce_password_policy(new_password, email=email_normalized)
    except PolicyViolation as exc:
        print(f"[FAIL] 비밀번호 정책 위반: {exc.sub_code} — {exc.message}")
        return 1
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(
            func.lower(User.email) == email_normalized, User.deleted_at.is_(None)
        )
        user = (await session.execute(stmt)).scalars().first()
        if user is None:
            print(f"[FAIL] 사용자 없음 email={email_normalized}")
            return 1
        await session.execute(
            update(User)
            .where(User.user_id == user.user_id)
            .values(password_hash=hash_password(new_password))
        )
        await session.commit()
        # 모든 refresh family revoke
        settings = get_settings()
        redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await revoke_all_user_refresh(user.user_id, redis)
        finally:
            await redis.aclose()
        print(
            f"[OK] 비밀번호 재설정 user_id={user.user_id} email={user.email}. "
            "모든 refresh 토큰 폐기 — 사용자 재로그인 필요."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="사용자 비밀번호 강제 변경")
    parser.add_argument("--email", required=True)
    parser.add_argument("--new-password", required=True, dest="new_password")
    args = parser.parse_args(argv)
    return asyncio.run(_reset(args.email, args.new_password))


if __name__ == "__main__":
    sys.exit(main())
