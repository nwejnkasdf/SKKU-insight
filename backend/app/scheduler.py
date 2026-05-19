"""rq-scheduler 부트스트랩 — 7 cron job 등록.

본 모듈은 `python -m app.scheduler` 로 one-shot 실행 (docker-compose worker 컨테이너의
entrypoint 직전 또는 Makefile `register-cron` 타깃에서). 이미 등록된 cron 은 skip
(idempotent — job_id 를 cron name 으로 고정).

cron 등록 ownership (docs/sdd/agent-orchestration.md §5):
- collection_job: A2 stub → A4 본문 (v13 라운드 — LLM tool-use)
- interest_decay_job: A2 stub → A6 본문
- merge_evaluation_job: A2 stub → A7 본문 (주 1회 leaf 병합 LLM)
- leaf_lifecycle_job (A7 신규): collection 직후 30분 (decision #14) — emerging 식별 LLM
- trace_merge_job (A7 신규): daily 18 UTC (decision #23) — trace 병합 LLM
- daily_lifecycle_evaluation_job (A7 신규): daily 18 UTC (decision #7/#13) — trace 강등 + leaf 강등 통합
- user_profile_generation_job (A8-v2 신규): daily 19 UTC (decisions.md §15) — archive x current
  fusion + reincarnation seeds 생성. discovery slot 2 의 input SOR.

cold_start_job 과 account_deletion_job 은 event-driven (cron 아님) → enqueue 만, 등록 X.

(v13 라운드 폐기, 2026-05-11) naver_cleanup_job 등록 제거 — decision-backlog P1-6 무효
(NaverBS4 어댑터 폐기). worker/jobs/naver_cleanup.py 파일과 NAVER_CLEANUP_CRON env 는
향후 News 소스 재활성화 가능성 위해 보존만.
"""
from __future__ import annotations

import logging
import sys
from typing import TypedDict

import redis as sync_redis
from rq_scheduler import Scheduler

from app.config import get_settings

logger = logging.getLogger(__name__)


class _JobRegistration(TypedDict):
    """rq-scheduler cron job 등록 spec."""

    id: str
    cron_attr: str
    func: str
    queue: str
    timeout: int


JOB_REGISTRATIONS: list[_JobRegistration] = [
    {
        "id": "collection_job",
        "cron_attr": "COLLECTION_CRON",
        "func": "app.worker.jobs.collection.collection_job",
        "queue": "default",
        "timeout": 7200,
    },
    {
        "id": "interest_decay_job",
        "cron_attr": "INTEREST_DECAY_CRON",
        "func": "app.worker.jobs.interest_decay.interest_decay_job",
        "queue": "default",
        "timeout": 1200,
    },
    {
        "id": "merge_evaluation_job",
        "cron_attr": "MERGE_EVALUATION_CRON",
        "func": "app.worker.jobs.merge_evaluation.merge_evaluation_job",
        "queue": "merge_evaluation",
        "timeout": 3600,
    },
    # === A7 신규 (2026-05-17) ===
    {
        "id": "leaf_lifecycle_job",
        "cron_attr": "LEAF_LIFECYCLE_CRON",
        "func": "app.worker.jobs.leaf_lifecycle.leaf_lifecycle_job",
        "queue": "default",
        "timeout": 3600,
    },
    {
        "id": "trace_merge_job",
        "cron_attr": "TRACE_MERGE_CRON",
        "func": "app.worker.jobs.trace_merge.trace_merge_job",
        "queue": "default",
        "timeout": 3600,
    },
    {
        # daily_lifecycle_evaluation 도 TRACE_MERGE_CRON 와 같은 시각 (18 UTC).
        # user-mutex (traversal_lock) 가 trace_merge_job 과 동일 — 같은 시점 사용자 trace
        # mutation 직렬화. (R2-RG-1 fix) leaf-level lock (merge_evaluation_lock) 과는 별개.
        "id": "daily_lifecycle_evaluation_job",
        "cron_attr": "TRACE_MERGE_CRON",
        "func": "app.worker.jobs.daily_lifecycle_evaluation.daily_lifecycle_evaluation_job",
        "queue": "default",
        "timeout": 1800,
    },
    # === A8-v2 신규 (2026-05-19, decisions.md §15) ===
    # daily 19 UTC — A6/A7 의 18 UTC 와 분리. user-mutex
    # (user_profile_generation_lock) 가 traversal_lock 과 독립 — profile 생성은
    # read-only + INSERT ON CONFLICT 만이라 A7 trace mutation 과 병행 가능.
    # timeout 5400s — 사용자당 LLM ~30s 가정 시 ~100명 + 마진.
    {
        "id": "user_profile_generation_job",
        "cron_attr": "USER_PROFILE_CRON",
        "func": "app.worker.jobs.user_profile.user_profile_generation_job",
        "queue": "default",
        "timeout": 5400,
    },
]


def register_cron_jobs() -> None:
    """7 cron job 등록 (idempotent). 이미 같은 id 가 있으면 cancel 후 재등록.

    A7 신규 (leaf_lifecycle / trace_merge / daily_lifecycle_evaluation) + A8-v2 신규
    (user_profile_generation) 포함 총 7 job.
    """
    settings = get_settings()
    conn = sync_redis.Redis.from_url(settings.REDIS_URL_QUEUE)
    scheduler = Scheduler(queue_name="default", connection=conn)
    try:
        # idempotent: 기존 job 을 list 로 1회만 materialize (rq-scheduler 의 get_jobs() 가
        # generator 인 점을 고려해 두 번째 iter 시 빈 결과가 되지 않도록 보장).
        existing_jobs = list(scheduler.get_jobs())
        existing_by_id: dict[str, object] = {}
        for job in existing_jobs:
            existing_by_id[job.id] = job
        for reg in JOB_REGISTRATIONS:
            job_id = reg["id"]
            cron_expr = getattr(settings, reg["cron_attr"])
            existing = existing_by_id.get(job_id)
            if existing is not None:
                scheduler.cancel(existing)
                logger.info("scheduler: cancelled existing job_id=%s", job_id)
            scheduler.cron(
                cron_expr,
                func=reg["func"],
                queue_name=reg["queue"],
                timeout=reg["timeout"],
                id=job_id,
                use_local_timezone=False,
            )
            logger.info(
                "scheduler: registered job_id=%s cron=%s func=%s",
                job_id,
                cron_expr,
                reg["func"],
            )
    finally:
        conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    register_cron_jobs()
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["JOB_REGISTRATIONS", "register_cron_jobs"]
