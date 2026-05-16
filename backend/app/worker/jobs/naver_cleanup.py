"""naver_cleanup worker stub — (v13 라운드 폐기, 2026-05-11).

decision-backlog P1-6 무효 — NaverBS4 어댑터 폐기로 본 cleanup job 자체가 불필요.
`scheduler.py` 의 JOB_REGISTRATIONS 에서 등록 제거됨. NAVER_CLEANUP_CRON env 와 본 파일은
향후 News 소스 재활성화 가능성 위해 보존만.

원래 책임 (보존용 기록): 30 day+ 지난 tech_news Document 정리.
"""
from __future__ import annotations


def naver_cleanup_job() -> None:
    """A4 에서 구현. cron 인자 없음."""
    raise NotImplementedError(
        "naver_cleanup_job 본문은 A4 (collection) 에이전트 책임. "
        "decision-backlog P1-6 참조."
    )


__all__ = ["naver_cleanup_job"]
