"""C-61 simulate worker — admin SUPER `POST /admin/users/{id}/simulate` 대상.

scripts/simulate_user_day.py 의 cli 를 subprocess 로 호출 (격리 + 검증된 패턴 재사용).

mode (admin schema SimulateRequest.mode):
- next_day  — active_day +1 + interest_decay + daily_lifecycle_evaluation (수집 X)
- full_day  — next_day + collection_job (LLM web_search)
- weekly    — leaf_lifecycle + trace_merge + user_profile_generation (한 번)

`days` (next_day / full_day) 만큼 반복. 매 1일 후 user.active_day_counter 가 7 배수면
weekly chain 자동 실행 — "더 큰 시간 단위도 정합" 위해 14/21/... 도 같은 룰로 발동.

진행률 Redis `simulate:{user_id}:status` 갱신 → admin SPA polling 대상.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import redis as sync_redis
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.contracts import RedisKey

logger = logging.getLogger(__name__)

_STATUS_TTL_SECONDS = 3600
_NEXT_DAY_TIMEOUT = 300.0  # interest_decay + daily_lifecycle ~ 30s 여유
_COLLECTION_TIMEOUT = 600.0  # LLM web_search 60~180s 여유
_WEEKLY_TIMEOUT = 900.0  # leaf_lifecycle + trace_merge + user_profile ~ 5분 여유


def _status_key(user_id: UUID) -> str:
    """admin schema SimulateStatusResponse 와 같은 namespace."""
    return RedisKey.simulate_status(user_id)


def _set_status(
    redis_conn: sync_redis.Redis, user_id: UUID, **fields: Any
) -> None:
    """기존 status 위에 fields 덮어쓰기. SETEX 1h 갱신."""
    key = _status_key(user_id)
    raw = redis_conn.get(key)
    state: dict[str, Any] = json.loads(raw) if raw else {}
    state.update(fields)
    redis_conn.setex(key, _STATUS_TTL_SECONDS, json.dumps(state, default=str))


def _call_simulate(cmd: str, user_id_str: str, timeout: float) -> tuple[bool, str]:
    """scripts/simulate_user_day.py {cmd} --user-id {uid} subprocess.

    docker 컨테이너 WORKDIR=/app (backend mount root). cli 가 활성 검증된 패턴.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.simulate_user_day",
            cmd,
            "--user-id",
            user_id_str,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (proc.stdout + proc.stderr)[-800:]
    return proc.returncode == 0, output


def _read_active_day(user_id: UUID) -> int:
    """user.active_day_counter sync 조회 — weekly auto-chain 분기 입력.

    next-day subprocess 가 이미 +1 commit 후라 RETURNING 값 받을 수 있지만 cli 출력
    파싱은 약함. 별도 짧은 sync select 가 더 안전.
    """
    settings = get_settings()
    sync_url = _to_sync_url(settings.DATABASE_URL)
    engine = create_engine(sync_url, pool_pre_ping=True, pool_size=1, max_overflow=0)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text('SELECT active_day_counter FROM "user" WHERE user_id = :uid'),
                {"uid": user_id},
            ).first()
        return int(row.active_day_counter) if row else 0
    finally:
        engine.dispose()


def _to_sync_url(async_url: str) -> str:
    """asyncpg → psycopg (account_deletion.py 와 동일 변환)."""
    if async_url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + async_url[len("postgresql+asyncpg://") :]
    return async_url


def simulate_user_day_job(user_id_str: str, mode: str, days: int) -> None:
    """RQ entrypoint. admin actions_service.enqueue_simulate 가 enqueue.

    Args:
        user_id_str: 대상 user.user_id 의 str(UUID).
        mode: 'next_day' | 'full_day' | 'weekly'.
        days: next_day/full_day 시 반복 횟수 (weekly 에서 무시). 1 이상.
    """
    logger.info(
        "simulate_user_day_job start user=%s mode=%s days=%d",
        user_id_str,
        mode,
        days,
    )
    settings = get_settings()
    redis_conn = sync_redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    user_id = UUID(user_id_str)
    started = datetime.now(UTC).isoformat()
    _set_status(
        redis_conn,
        user_id,
        state="running",
        mode=mode,
        days_total=(0 if mode == "weekly" else days),
        days_done=0,
        weekly_chains=0,
        started_at=started,
        finished_at=None,
        message=None,
    )
    try:
        chains = 0
        if mode == "weekly":
            ok, out = _call_simulate("weekly", user_id_str, _WEEKLY_TIMEOUT)
            if not ok:
                raise RuntimeError(f"weekly subprocess failed: {out}")
        else:
            for i in range(days):
                # 1. next-day (interest_decay + daily_lifecycle_evaluation)
                ok, out = _call_simulate("next-day", user_id_str, _NEXT_DAY_TIMEOUT)
                if not ok:
                    raise RuntimeError(f"next-day day#{i + 1} failed: {out}")
                # 2. (full_day only) collection_job
                if mode == "full_day":
                    ok, out = _call_simulate(
                        "collection", user_id_str, _COLLECTION_TIMEOUT
                    )
                    if not ok:
                        # collection 실패는 chain 중단 X — 운영 cron 동일 정책.
                        logger.warning(
                            "collection failed day#%d user=%s tail=%s",
                            i + 1,
                            user_id,
                            out[-200:],
                        )
                # 3. weekly auto-chain — active_day % 7 == 0 도달 시.
                ad = _read_active_day(user_id)
                if ad > 0 and ad % 7 == 0:
                    ok, out = _call_simulate("weekly", user_id_str, _WEEKLY_TIMEOUT)
                    if not ok:
                        raise RuntimeError(
                            f"weekly auto-chain ad={ad} failed: {out}"
                        )
                    chains += 1
                _set_status(
                    redis_conn,
                    user_id,
                    days_done=i + 1,
                    weekly_chains=chains,
                )
        finished = datetime.now(UTC).isoformat()
        _set_status(
            redis_conn,
            user_id,
            state="succeeded",
            finished_at=finished,
            message=None,
        )
    except Exception as exc:
        finished = datetime.now(UTC).isoformat()
        _set_status(
            redis_conn,
            user_id,
            state="failed",
            finished_at=finished,
            message=str(exc)[:300],
        )
        logger.exception("simulate_user_day_job failed user=%s", user_id)
        raise
    finally:
        redis_conn.close()
    logger.info("simulate_user_day_job done user=%s mode=%s", user_id_str, mode)


__all__ = ["simulate_user_day_job"]
