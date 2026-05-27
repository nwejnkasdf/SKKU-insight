"""simulate_persona_dohyun_42days.py — 김도현 페르소나 42일 시뮬레이션.

Day 0 시드 (`seed_persona_dohyun.py`) 후 실행. 42일 동안 일자별 인터랙션 +
worker job 호출로 trace lifecycle 시연 데이터 생성.

매 day 흐름:
1. (선택) collection_job — corpus 누적 (LLM web_search). COLLECTION_DAYS 만.
2. SKIP_DAYS 아니면:
   2-1. 인터랙션 ingest (UserEvent + Bayesian + score_tail sync + mark_stale)
   2-2. active_day +1
   2-3. interest_decay_job (decay)
   2-4. daily_lifecycle_evaluation_job (extend/split/retract/archive 평가)
3. (선택) leaf_lifecycle_job — emerging leaf 생성 (LLM). LEAF_LIFECYCLE_DAYS 만.
4. snapshot 출력

시연 timeline (active_day 트래킹):
  Day 1-4  : NLP 6건 → Day 5 daily cron 에서 extend `AI → NLP`
  Day 7-11 : IR + ML 양쪽 5건씩 → Day 12 split (`→ IR`, `→ ML` 두 가지)
  Day 13-19: IR 활발, ML 무시 (RAG 영역 누적)
  Day 20-26: 미접속 (중간고사) — active_day 유지
  Day 27-32: 복귀, RAG 집중 → Day 32 leaf_lifecycle 호출 → "RAG" leaf
  Day 33-42: 계속 RAG, ML idle 누적 → Day 42 ML stale 마킹 (idle=23)

LLM 호출 총 6회 (collection 5 + leaf 1).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import EventType
from app.db.engine import get_engine
from app.db.models import CSOTopic, User
from app.db.session import AsyncSessionLocal
from app.interest.config_loader import (
    load_system_config,
)
from app.interest.service import ingest_event_atomic
from app.redis import get_redis
from app.topic.graph import build_cso_graph

EMAIL = "dohyun@demo.skku.ac.kr"

logger = logging.getLogger("simulate_persona_dohyun")


# ============================================================
# Daily interaction pattern — (clicks, saves, hides) per topic label
# ============================================================

# 모든 topic label 은 CSO 의 정규화된 label (lowercase). 시뮬레이션이 label 매칭으로
# CSO ID lookup → 그 CSO 매핑 docs 중 사용자가 안 본 docs click/save/hide.

DAILY_PATTERN: dict[int, dict[str, tuple[int, int, int]]] = {
    # Phase 1: NLP extend (Day 1-4, 누적 6건 → Day 5 cron 에서 extend)
    1: {"natural language processing": (2, 0, 0)},
    2: {"natural language processing": (1, 0, 0)},
    3: {"natural language processing": (1, 1, 0)},
    4: {"natural language processing": (1, 0, 0)},
    5: {},  # 인터랙션 0 — daily cron 이 extend 평가
    # Phase 2: NLP 영역 corpus 더 풍부히 (Day 6 collection)
    6: {"natural language processing": (1, 0, 0)},
    # Phase 3: IR + ML 양쪽 분산 (Day 7-11, 각 5건 → Day 12 cron 에서 split)
    7: {"information retrieval": (2, 0, 0)},
    8: {"information retrieval": (1, 0, 0), "machine learning": (2, 0, 0)},
    9: {"information retrieval": (1, 0, 0), "machine learning": (1, 0, 0)},
    10: {"information retrieval": (1, 0, 0), "machine learning": (1, 1, 0)},
    11: {"machine learning": (1, 0, 0)},
    12: {},  # 인터랙션 0 — daily cron 이 split 평가
    # Phase 4: IR 가지 활발, ML 가지 무시 시작 (Day 13-19)
    13: {"information retrieval": (2, 0, 0)},
    14: {"information retrieval": (1, 1, 0)},
    15: {"information retrieval": (1, 0, 0)},  # Day 15 collection trigger
    16: {"information retrieval": (1, 1, 0)},
    17: {"information retrieval": (2, 0, 0)},
    18: {"information retrieval": (1, 0, 0)},
    19: {"information retrieval": (1, 1, 0)},
    # Phase 5: 중간고사 미접속 (Day 20-26) — SKIP_DAYS 로 처리
    # (active_day 안 늘어남, 인터랙션 없음, daily cron 도 안 돔)
    # Phase 6: 복귀 + RAG 집중 (Day 27-32, leaf 후보 누적)
    27: {"information retrieval": (2, 1, 0)},
    28: {"information retrieval": (1, 1, 0)},
    29: {"information retrieval": (2, 0, 0)},
    30: {"information retrieval": (1, 1, 0)},
    31: {"information retrieval": (2, 1, 0)},
    32: {"information retrieval": (1, 0, 0)},  # Day 32 leaf_lifecycle trigger
    # Phase 7: 계속 RAG (Day 33-42), ML 영역 idle 누적
    33: {"information retrieval": (1, 1, 0)},
    34: {"information retrieval": (2, 0, 0)},
    35: {"information retrieval": (1, 1, 0)},
    36: {"information retrieval": (1, 0, 0)},  # Day 36 collection trigger
    37: {"information retrieval": (1, 1, 0)},
    38: {"information retrieval": (2, 0, 0)},
    39: {"information retrieval": (1, 1, 0)},
    40: {"information retrieval": (1, 0, 0)},
    41: {"information retrieval": (1, 1, 0)},
    42: {"information retrieval": (1, 0, 0)},
}

SKIP_DAYS: frozenset[int] = frozenset(range(20, 27))  # 중간고사
COLLECTION_DAYS: frozenset[int] = frozenset({1, 6, 15, 27, 36})
LEAF_LIFECYCLE_DAYS: frozenset[int] = frozenset({32})


# ============================================================
# subprocess helpers — LLM-heavy worker job 호출 (simulate_user_day 패턴)
# ============================================================

def _call_job(module: str, fn: str, *args: object, timeout: float) -> tuple[bool, str]:
    """fresh subprocess 로 worker job 호출 — event loop / redis client race 회피."""
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


def _extract_log_tail(output: str, marker: str) -> str:
    matches = [line for line in output.splitlines() if marker in line]
    if matches:
        return matches[-1].strip()[:200]
    tail = output.strip().splitlines()
    return tail[-1][:200] if tail else "(no output)"


# ============================================================
# In-process helpers — 가벼운 DB op + cron job 직접 await
# ============================================================

async def _resolve_user(db: AsyncSession) -> User:
    user = (
        await db.execute(select(User).where(func.lower(User.email) == EMAIL.lower()))
    ).scalar_one_or_none()
    if user is None:
        raise SystemExit(
            f"user {EMAIL} 미발견 — 먼저 `python -m scripts.seed_persona_dohyun`"
        )
    return user


async def _bump_active_day(db: AsyncSession, user_id: UUID) -> int:
    result = await db.execute(
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
    await db.commit()
    return int(new_ad)


async def _lookup_cso(db: AsyncSession, label: str) -> UUID | None:
    """label → CSO ID. underscore/space 양쪽 흡수."""
    target = label.lower().replace("_", " ").strip()
    stmt = select(CSOTopic.cso_topic_id).where(
        func.lower(func.replace(CSOTopic.label, "_", " ")) == target
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _docs_for_topic(
    db: AsyncSession, user_id: UUID, cso_topic_id: UUID, *, limit: int
) -> list[tuple[UUID, UUID]]:
    """해당 CSO 매핑 docs 중 사용자가 안 본 것. (doc_id, cso_id) list."""
    stmt = text(
        """
        SELECT d.document_id, dt.cso_topic_id
        FROM document d
        JOIN document_topic dt ON dt.document_id = d.document_id
        WHERE dt.cso_topic_id = :cso
          AND d.content_type != 'pseudo_cold_start'
          AND NOT EXISTS (
              SELECT 1 FROM user_event ue
              WHERE ue.user_id = :u
                AND ue.document_id = d.document_id
                AND ue.event_type IN ('click', 'save', 'hide')
          )
        ORDER BY d.published_at DESC NULLS LAST
        LIMIT :lim
        """
    )
    rows = (
        await db.execute(stmt, {"cso": cso_topic_id, "u": user_id, "lim": limit})
    ).all()
    return [(r.document_id, r.cso_topic_id) for r in rows]


async def _ingest_day(
    db: AsyncSession,
    redis: Any,
    cso_graph: Any,
    user: User,
    day: int,
    pattern: dict[str, tuple[int, int, int]],
) -> dict[str, int]:
    """본 day 의 인터랙션 패턴을 ingest_event_atomic 으로 처리. 통계 dict 반환."""
    settings = get_settings()
    params, weights = await load_system_config(db, redis)
    # 시뮬레이션의 "오늘" 시각 — 실제 datetime.now 사용 (active_day 와는 별개 트래킹)
    now = datetime.now(UTC)
    counts = {"click": 0, "save": 0, "hide": 0, "skipped_no_docs": 0}
    for topic_label, (n_click, n_save, n_hide) in pattern.items():
        cso_id = await _lookup_cso(db, topic_label)
        if cso_id is None:
            logger.warning("topic '%s' CSO 미발견 — skip", topic_label)
            continue
        total = n_click + n_save + n_hide
        docs = await _docs_for_topic(db, user.user_id, cso_id, limit=total)
        if not docs:
            counts["skipped_no_docs"] += 1
            logger.warning(
                "day=%d topic=%s 매핑 docs 없음 — skip (corpus 부족 가능성)",
                day, topic_label,
            )
            continue
        # (click, save, hide) 순서로 docs 소비
        idx = 0
        for kind, n in (
            (EventType.CLICK, n_click),
            (EventType.SAVE, n_save),
            (EventType.HIDE, n_hide),
        ):
            for _ in range(n):
                if idx >= len(docs):
                    break
                doc_id, _doc_cso = docs[idx]
                idx += 1
                try:
                    await ingest_event_atomic(
                        db,
                        redis,
                        cso_graph,
                        settings,
                        params,
                        weights,
                        user=user,
                        event_type=kind,
                        document_id=doc_id,
                        cso_topic_id=None,  # DocumentTopic 자동 분배
                        leaf_topic_id=None,
                        dwell_ms=None,
                        client_request_id=f"sim-{user.user_id}-d{day}-{uuid.uuid4().hex[:8]}",
                        occurred_at=now,
                        active_day=int(user.active_day_counter),
                        cache_invalidate=False,
                    )
                    await db.commit()
                    counts[kind.value] += 1
                except Exception as e:
                    await db.rollback()
                    logger.warning(
                        "day=%d ingest 실패 topic=%s kind=%s doc=%s err=%s",
                        day, topic_label, kind.value, doc_id, e,
                    )
    return counts


async def _snapshot(db: AsyncSession, user_id: UUID) -> str:
    row = (
        await db.execute(
            text(
                """
                SELECT
                  (SELECT active_day_counter FROM "user" WHERE user_id = :u) AS ad,
                  (SELECT count(*) FROM user_cso_traversal WHERE user_id = :u AND status = 'active') AS active_t,
                  (SELECT count(*) FROM user_cso_traversal WHERE user_id = :u AND status = 'stale') AS stale_t,
                  (SELECT count(*) FROM user_cso_traversal WHERE user_id = :u AND status = 'archived') AS arch_t,
                  (SELECT count(*) FROM dynamic_leaf_topic WHERE user_id = :u AND status IN ('emerging','active')) AS live_leaves,
                  (SELECT count(*) FROM user_event WHERE user_id = :u) AS events,
                  (SELECT max(array_length(path,1)) FROM user_cso_traversal WHERE user_id = :u AND status IN ('active','stale')) AS max_pl
                """
            ),
            {"u": user_id},
        )
    ).first()
    if row is None:
        return "user not found"
    return (
        f"ad={row.ad} traces[active={row.active_t} stale={row.stale_t} "
        f"arch={row.arch_t}] leaves={row.live_leaves} events={row.events} "
        f"max_path={row.max_pl}"
    )


# ============================================================
# Main loop
# ============================================================

async def _run_simulation(start_day: int, end_day: int) -> None:
    """start_day..end_day 범위만 시뮬레이션 (재실행 / 부분 실행 지원)."""
    engine = get_engine()
    redis = get_redis("default")
    cso_graph = await build_cso_graph(engine)

    async with AsyncSessionLocal() as db:
        user = await _resolve_user(db)
        user_id = user.user_id
        logger.info(
            "시뮬레이션 시작 user_id=%s start_day=%d end_day=%d (현재 %s)",
            user_id, start_day, end_day, await _snapshot(db, user_id),
        )

    for day in range(start_day, end_day + 1):
        print(f"\n========== Day {day} ==========", flush=True)

        # 1. Collection (LLM, subprocess)
        if day in COLLECTION_DAYS:
            ok, out = _call_job(
                "app.worker.jobs.collection",
                "collection_job",
                str(user_id),
                timeout=300.0,
            )
            tag = "OK" if ok else "FAIL"
            print(f"  [{tag}] collection_job: {_extract_log_tail(out, 'collection_job')}", flush=True)

        # 2. Ingest + active_day +1 + cron jobs
        if day in SKIP_DAYS:
            print("  [SKIP] 미접속 (시험기간)", flush=True)
        else:
            pattern = DAILY_PATTERN.get(day, {})
            async with AsyncSessionLocal() as db:
                user = await _resolve_user(db)  # active_day_counter refresh
                counts = await _ingest_day(db, redis, cso_graph, user, day, pattern)
                new_ad = await _bump_active_day(db, user_id)
            print(
                f"  ingest click={counts['click']} save={counts['save']} "
                f"hide={counts['hide']} (active_day {new_ad})",
                flush=True,
            )

            # interest_decay_job (lightweight SQL)
            ok, out = _call_job(
                "app.worker.jobs.interest_decay",
                "interest_decay_job",
                timeout=60.0,
            )
            tag = "OK" if ok else "FAIL"
            print(
                f"  [{tag}] interest_decay: {_extract_log_tail(out, 'interest_decay_job')}",
                flush=True,
            )

            # daily_lifecycle_evaluation_job (extend/split/retract/archive)
            ok, out = _call_job(
                "app.worker.jobs.daily_lifecycle_evaluation",
                "daily_lifecycle_evaluation_job",
                timeout=120.0,
            )
            tag = "OK" if ok else "FAIL"
            print(
                f"  [{tag}] daily_lifecycle: {_extract_log_tail(out, 'daily_lifecycle_evaluation_job')}",
                flush=True,
            )

        # 3. Leaf lifecycle (LLM, subprocess)
        if day in LEAF_LIFECYCLE_DAYS:
            ok, out = _call_job(
                "app.worker.jobs.leaf_lifecycle",
                "leaf_lifecycle_job",
                timeout=240.0,
            )
            tag = "OK" if ok else "FAIL"
            print(f"  [{tag}] leaf_lifecycle: {_extract_log_tail(out, 'leaf_lifecycle_job')}", flush=True)

        # 4. Snapshot
        async with AsyncSessionLocal() as db:
            print(f"  -> {await _snapshot(db, user_id)}", flush=True)

    print("\n시뮬레이션 완료.", flush=True)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        prog="simulate_persona_dohyun_42days",
        description="김도현 페르소나 42일 시뮬레이션 — Day 0 seed 이후 실행.",
    )
    parser.add_argument(
        "--from-day", type=int, default=1, help="시작 day (default 1)"
    )
    parser.add_argument(
        "--to-day", type=int, default=42, help="종료 day (default 42, inclusive)"
    )
    args = parser.parse_args(argv)
    if args.from_day < 1 or args.to_day > 42 or args.from_day > args.to_day:
        raise SystemExit("--from-day, --to-day 는 1..42 범위 + from ≤ to")
    asyncio.run(_run_simulation(args.from_day, args.to_day))
    return 0


if __name__ == "__main__":
    sys.exit(main())
