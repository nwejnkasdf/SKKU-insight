"""RQ job 함수 — A2 범위.

- `account_deletion.py`: A2 가 본문 구현 (1차 시연 RQ async, decision-backlog C-2 부분 해소)
- `cold_start.py`: A8 가 본문 구현 (현재 stub)
- `naver_cleanup.py`: A4 (현재 stub, decision-backlog P1-6)
- `collection.py`: A4 (현재 stub)
- `interest_decay.py`: A6 (현재 stub)
- `merge_evaluation.py`: A7 (현재 stub)

본 __init__.py 가 모든 모듈을 import 해야 RQ unpickle 이 동작.
"""
from __future__ import annotations

from app.worker.jobs import (
    account_deletion,
    cold_start,
    collection,
    interest_decay,
    merge_evaluation,
    naver_cleanup,
)

__all__ = [
    "account_deletion",
    "cold_start",
    "collection",
    "interest_decay",
    "merge_evaluation",
    "naver_cleanup",
]
