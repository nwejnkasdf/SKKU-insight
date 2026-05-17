"""RQ job 함수 — A2~A7 범위.

- `account_deletion.py`: A2 본문 (RQ async cascade)
- `cold_start.py`: A8 (현재 stub)
- `naver_cleanup.py`: v13 라운드 폐기 (decision-backlog P1-6)
- `collection.py`: A4 본문 (v13 라운드 LLM tool-use)
- `interest_decay.py`: A6 본문 (daily 18 UTC)
- `merge_evaluation.py`: A7 본문 (주간 leaf 병합)
- `leaf_lifecycle.py`: A7 신규 (collection 직후 30분 — emerging 식별)
- `trace_merge.py`: A7 신규 (daily 18 UTC — trace 병합)
- `daily_lifecycle_evaluation.py`: A7 신규 (daily 18 UTC — trace+leaf 강등 통합)

본 __init__.py 가 모든 모듈을 import 해야 RQ unpickle 이 동작.
"""
from __future__ import annotations

from app.worker.jobs import (
    account_deletion,
    cold_start,
    collection,
    daily_lifecycle_evaluation,
    interest_decay,
    leaf_lifecycle,
    merge_evaluation,
    naver_cleanup,
    trace_merge,
)

__all__ = [
    "account_deletion",
    "cold_start",
    "collection",
    "daily_lifecycle_evaluation",
    "interest_decay",
    "leaf_lifecycle",
    "merge_evaluation",
    "naver_cleanup",
    "trace_merge",
]
