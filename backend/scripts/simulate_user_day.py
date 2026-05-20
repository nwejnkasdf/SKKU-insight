"""day-by-day 수동 시뮬레이션 helper — P1-12 fix 검증 + 시연 narrative 보조.

backend 의 daily 18 UTC cron 을 기다리지 않고, 같은 worker job 함수를 직접
호출해서 시간 가속. 실 사용자의 인터랙션 (client 가 보낸 user_event) 위에서
"하루 지난 척" 평가하는 게 본 도구의 의도.

명령 3종:
  next-day   active_day +1 → interest_decay_job + daily_lifecycle_evaluation_job
             (= P1-12 extend/split caller 포함)
  collection 단일 user 대상 collection_job (LLM web_search 1회)
  weekly     leaf_lifecycle_job + trace_merge_job + user_profile_generation_job

사용:
  docker compose exec api python -m scripts.simulate_user_day next-day \\
      --user-email user@example.com
  make next-day EMAIL=user@example.com

자세히는 docs/ops/manual-day-control.md.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import get_settings

logger = logging.getLogger("simulate_user_day")


async def _resolve_user_id(
    engine: AsyncEngine, email: str | None, user_id: str | None
) -> UUID:
    """email 또는 user_id 인자에서 UUID 확정. 둘 다 미지정/오타 시 SystemExit."""
    if user_id:
        try:
            return UUID(user_id)
        except ValueError as exc:
            raise SystemExit(f"--user-id 형식 오류: {exc}") from exc
    if not email:
        raise SystemExit(
            "--user-email 또는 --user-id 중 하나 필요. "
            "예: --user-email user@example.com"
        )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        row = (
            await session.execute(
                text('SELECT user_id FROM "user" WHERE email = :e'),
                {"e": email},
            )
        ).first()
    if row is None:
        raise SystemExit(f"user not found: email={email}")
    return row.user_id


async def _snapshot(engine: AsyncEngine, user_id: UUID) -> str:
    """현재 user 상태 한 줄 요약 — before/after 비교용."""
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT
                        (SELECT active_day_counter FROM "user" WHERE user_id = :u) AS ad,
                        (SELECT count(*) FROM user_cso_traversal
                         WHERE user_id = :u AND status = 'active') AS active_traces,
                        (SELECT count(*) FROM dynamic_leaf_topic
                         WHERE user_id = :u AND status IN ('emerging','active')) AS leaves,
                        (SELECT count(*) FROM user_event WHERE user_id = :u) AS events,
                        (SELECT count(*) FROM document
                         WHERE content_type != 'pseudo_cold_start'
                         AND (raw->>'demo_backfill') IS NULL) AS real_docs,
                        (SELECT max(array_length(path,1)) FROM user_cso_traversal
                         WHERE user_id = :u AND status = 'active') AS max_pl
                    """
                ),
                {"u": user_id},
            )
        ).first()
    if row is None:
        return "user not found"
    return (
        f"ad={row.ad} traces={row.active_traces} leaves={row.leaves} "
        f"events={row.events} real_docs={row.real_docs} max_path_len={row.max_pl}"
    )


async def _bump_active_day(engine: AsyncEngine, user_id: UUID) -> int:
    """active_day_counter +1, last_active_calendar_date 리셋. UPDATE … RETURNING 값."""
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        result = await session.execute(
            text(
                'UPDATE "user" '
                "SET active_day_counter = active_day_counter + 1, "
                "    last_active_calendar_date = NULL "
                "WHERE user_id = :uid "
                "RETURNING active_day_counter"
            ),
            {"uid": user_id},
        )
        new_ad = result.scalar_one()
        await session.commit()
    return int(new_ad)


def _call_job(module: str, fn: str, *args: object, timeout: float) -> tuple[bool, str]:
    """fresh subprocess 로 worker job 호출 — event loop / redis client race 회피.

    Returns: (success, combined stdout+stderr).
    """
    arg_repr = ", ".join(repr(a) for a in args)
    code = f"from {module} import {fn}; {fn}({arg_repr})"
    try:
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout}s"
    return result.returncode == 0, result.stdout + result.stderr


def _extract_metric_line(output: str, marker: str) -> str:
    """worker log 에서 marker 포함 마지막 줄 추출. 없으면 tail."""
    matches = [line for line in output.splitlines() if marker in line]
    if matches:
        return matches[-1].strip()[:200]
    return output.strip().splitlines()[-1][:200] if output.strip() else "(no output)"


async def _cmd_next_day(engine: AsyncEngine, user_id: UUID) -> None:
    """active_day +1 + interest_decay + daily_lifecycle_evaluation (extend/split 포함)."""
    print(f"[before] {await _snapshot(engine, user_id)}", flush=True)
    new_ad = await _bump_active_day(engine, user_id)
    print(f"[bump]   active_day_counter -> {new_ad}", flush=True)
    for module, fn, marker, timeout in [
        ("app.worker.jobs.interest_decay", "interest_decay_job",
         "interest_decay_job", 60.0),
        ("app.worker.jobs.daily_lifecycle_evaluation",
         "daily_lifecycle_evaluation_job",
         "daily_lifecycle_evaluation_job", 120.0),
    ]:
        ok, out = _call_job(module, fn, timeout=timeout)
        status = "OK  " if ok else "FAIL"
        print(f"  {status} {fn}: {_extract_metric_line(out, marker)}", flush=True)
    print(f"[after]  {await _snapshot(engine, user_id)}", flush=True)


async def _cmd_collection(engine: AsyncEngine, user_id: UUID) -> None:
    """단일 user 대상 collection_job — LLM web_search 1회 (60-180s)."""
    print(f"[before] {await _snapshot(engine, user_id)}", flush=True)
    ok, out = _call_job(
        "app.worker.jobs.collection",
        "collection_job",
        str(user_id),
        timeout=300.0,
    )
    status = "OK  " if ok else "FAIL"
    print(f"  {status} collection_job", flush=True)
    metric = _extract_metric_line(out, "collection_job done")
    if metric != "(no output)":
        print(f"       {metric}", flush=True)
    print(f"[after]  {await _snapshot(engine, user_id)}", flush=True)


async def _cmd_weekly(engine: AsyncEngine, user_id: UUID) -> None:
    """leaf_lifecycle + trace_merge + user_profile_generation 3종 묶음."""
    print(f"[before] {await _snapshot(engine, user_id)}", flush=True)
    for module, fn, marker, timeout in [
        ("app.worker.jobs.leaf_lifecycle", "leaf_lifecycle_job",
         "leaf_lifecycle_job", 240.0),
        ("app.worker.jobs.trace_merge", "trace_merge_job",
         "trace_merge_job", 180.0),
        ("app.worker.jobs.user_profile", "user_profile_generation_job",
         "user_profile_job", 240.0),
    ]:
        ok, out = _call_job(module, fn, timeout=timeout)
        status = "OK  " if ok else "FAIL"
        print(f"  {status} {fn}: {_extract_metric_line(out, marker)}", flush=True)
    print(f"[after]  {await _snapshot(engine, user_id)}", flush=True)


async def _main_async(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        user_id = await _resolve_user_id(engine, args.user_email, args.user_id)
        if args.command == "next-day":
            await _cmd_next_day(engine, user_id)
        elif args.command == "collection":
            await _cmd_collection(engine, user_id)
        elif args.command == "weekly":
            await _cmd_weekly(engine, user_id)
        else:  # pragma: no cover — argparse 가 막음
            raise SystemExit(f"unknown command: {args.command}")
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulate_user_day",
        description=(
            "day-by-day 수동 시뮬레이션 helper (P1-12 fix 검증 + 시연 narrative). "
            "자세히는 docs/ops/manual-day-control.md"
        ),
    )
    parser.add_argument(
        "command",
        choices=["next-day", "collection", "weekly"],
        help="next-day: active_day+1 + daily lite jobs (extend/split 평가). "
        "collection: 단일 user LLM web_search. weekly: leaf_lifecycle + "
        "trace_merge + user_profile.",
    )
    parser.add_argument(
        "--user-email",
        help="대상 user.email. user_id 와 둘 중 하나 필수.",
    )
    parser.add_argument(
        "--user-id",
        help="대상 user_id (UUID). email 보다 우선.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=sys.stderr,
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
