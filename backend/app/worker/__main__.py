"""RQ worker entry — `python -m app.worker` 또는 docker-compose worker 서비스.

큐 우선순위: default → leaf_lifecycle → merge_evaluation → summary_generation.
`--with-scheduler` 옵션은 docker-compose 의 command 에서 셋팅 (rq worker --with-scheduler ...).

본 모듈은 jobs 패키지를 import 해 함수가 RQ 의 unpickle 시 발견 가능하도록 보장.
"""
from __future__ import annotations

import logging
import sys

import redis as sync_redis
from rq import Connection, Queue, Worker

from app.config import get_settings

# jobs 패키지 import — RQ 가 unpickle 시 함수 path 로 lookup 하므로 사전 import 필요
from app.worker import jobs  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main() -> None:
    settings = get_settings()
    conn = sync_redis.Redis.from_url(settings.REDIS_URL_QUEUE)
    queue_names = [
        "default",
        "leaf_lifecycle",
        "merge_evaluation",
        "summary_generation",
    ]
    with Connection(conn):
        queues = [Queue(name) for name in queue_names]
        worker = Worker(queues, connection=conn)
        worker.work(with_scheduler=True, logging_level="INFO")


if __name__ == "__main__":
    sys.exit(main() or 0)
