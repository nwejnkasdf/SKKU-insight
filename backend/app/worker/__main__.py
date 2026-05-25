"""RQ worker entry — `python -m app.worker` 또는 docker-compose worker 서비스.

큐 우선순위: default → leaf_lifecycle → merge_evaluation → summary_generation.

(C-65, 2026-05-26) RQ 2.x WorkerPool 사용 — 1 process 안에서 N worker spawn.
옛 단일 `Worker.work()` 가 동시 1 job 만 처리 → 여러 user 의 collection_job 순차 대기.
WorkerPool(num_workers=N) 으로 동시 N job 처리 가능.

본 모듈은 jobs 패키지를 import 해 함수가 RQ 의 unpickle 시 발견 가능하도록 보장.
"""
from __future__ import annotations

import logging
import sys

import redis as sync_redis
from rq import Queue
from rq.worker_pool import WorkerPool

from app.config import get_settings

# jobs 패키지 import — RQ 가 unpickle 시 함수 path 로 lookup 하므로 사전 import 필요
from app.worker import jobs  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main() -> int:
    """RQ WorkerPool entrypoint. (C-65, 2026-05-26)

    옛 단일 `Worker.work()` → `WorkerPool(num_workers=...)` 으로 변경. 한 process 안
    N worker 인스턴스 동시 실행. RQ scheduler 는 첫 worker 가 자동 acquire.
    """
    settings = get_settings()
    conn = sync_redis.Redis.from_url(settings.REDIS_URL_QUEUE)
    queue_names = ["default", "leaf_lifecycle", "merge_evaluation", "summary_generation"]
    queues = [Queue(name, connection=conn) for name in queue_names]
    pool = WorkerPool(
        queues=queues,
        connection=conn,
        num_workers=settings.WORKER_POOL_SIZE,
    )
    pool.start(logging_level="INFO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
