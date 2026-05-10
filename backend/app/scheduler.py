"""rq-scheduler 부트스트랩 — 4 cron job 등록.

본 모듈은 `python -m app.scheduler` 로 one-shot 실행 (docker-compose worker 컨테이너의
entrypoint 직전 또는 Makefile `register-cron` 타깃에서). 이미 등록된 cron 은 skip
(idempotent — job_id 를 cron name 으로 고정).

cron 등록 ownership (docs/sdd/agent-orchestration.md §5):
- naver_cleanup_job: A2 stub → A4 본문
- collection_job: A2 stub → A4 본문
- interest_decay_job: A2 stub → A6 본문
- merge_evaluation_job: A2 stub → A7 본문

cold_start_job 과 account_deletion_job 은 event-driven (cron 아님) → enqueue 만, 등록 X.
"""
from __future__ import annotations

import logging
import sys

import redis as sync_redis
from rq_scheduler import Scheduler

from app.config import get_settings

logger = logging.getLogger(__name__)


JOB_REGISTRATIONS = [
    {
        "id": "naver_cleanup_job",
        "cron_attr": "NAVER_CLEANUP_CRON",
        "func": "app.worker.jobs.naver_cleanup.naver_cleanup_job",
        "queue": "default",
        "timeout": 600,
    },
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
]


def register_cron_jobs() -> None:
    """4 cron job 등록 (idempotent). 이미 같은 id 가 있으면 cancel 후 재등록."""
    settings = get_settings()
    conn = sync_redis.Redis.from_url(settings.REDIS_URL_QUEUE)
    scheduler = Scheduler(queue_name="default", connection=conn)
    try:
        # idempotent: 기존 같은 id 의 job 모두 cancel
        existing_ids = {job.id for job in scheduler.get_jobs()}
        for reg in JOB_REGISTRATIONS:
            job_id = reg["id"]
            cron_expr = getattr(settings, reg["cron_attr"])
            if job_id in existing_ids:
                # 기존 job cancel
                for job in scheduler.get_jobs():
                    if job.id == job_id:
                        scheduler.cancel(job)
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    register_cron_jobs()


if __name__ == "__main__":
    sys.exit(main() or 0)


__all__ = ["JOB_REGISTRATIONS", "register_cron_jobs"]
