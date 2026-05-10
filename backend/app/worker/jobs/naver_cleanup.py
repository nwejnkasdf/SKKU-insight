"""naver_cleanup worker stub — A4 (collection) 가 본문 구현.

decision-backlog P1-6. cron = `NAVER_CLEANUP_CRON` (default `0 17 * * *` UTC).

A4 책임: 토픽 매핑이 사라지고 `created_at >= 30 day` 인 `content_type=tech_news` Document 삭제.
NFR-25 (외부 원문 무단 복제 금지) 정합.
"""
from __future__ import annotations


def naver_cleanup_job() -> None:
    """A4 에서 구현. cron 인자 없음."""
    raise NotImplementedError(
        "naver_cleanup_job 본문은 A4 (collection) 에이전트 책임. "
        "decision-backlog P1-6 참조."
    )


__all__ = ["naver_cleanup_job"]
