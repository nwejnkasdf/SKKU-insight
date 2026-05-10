"""collection worker stub — A4 (collection) 가 본문 구현.

cron = `COLLECTION_CRON` (default `0 3 * * *` UTC = KST 12:00).
A4 책임: 사용자별 어댑터 호출 (arXiv / OpenAlex / S2 / DBLP / RSS / 네이버) + dedup +
ClickbaitClassifier 호출 + Document INSERT + DocumentTopic 매핑.
"""
from __future__ import annotations


def collection_job() -> None:
    """A4 에서 구현. cron 인자 없음."""
    raise NotImplementedError("collection_job 본문은 A4 (collection) 에이전트 책임.")


__all__ = ["collection_job"]
