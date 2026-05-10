"""RQ worker 패키지 — entry, scheduler registration, job 함수.

본 패키지 import 자체는 가벼움 (jobs 는 별도 namespace).
worker 부트: `python -m app.worker` → __main__.py 실행.
scheduler 등록: `python -m app.scheduler` (one-shot, idempotent).
"""
from __future__ import annotations

__all__: list[str] = []
