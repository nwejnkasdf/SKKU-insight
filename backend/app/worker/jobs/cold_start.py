"""cold_start worker stub — A8 (recommendation) 가 본문 구현.

호출 시그니처는 onboarding.service._enqueue_cold_start_job 에서 enqueue 한 그대로:
  (request_id: str, user_id: str, cluster_ids: list[str], user_class: str, locale: str)

A8 책임:
1) Redis `cold_start:status:{request_id}` HSET status=running
2) LLMProvider 호출 → 10 후보 생성 (5 core / 3 adjacent / 2 discovery)
3) sentinel `Source(name='cold_start_pseudo')` 의 source_id 로 pseudo Document INSERT
4) UserInterestState prior boost (cluster_ids 의 cso_seed_topic_id)
5) Recommendation INSERT
6) HSET status=completed, dashboard_ready=true, completed_at=now()
"""
from __future__ import annotations


def cold_start_job(
    request_id: str,
    user_id: str,
    cluster_ids: list[str],
    user_class: str,
    locale: str,
) -> None:
    """A8 에서 구현. 시그니처는 호출 측(onboarding.service)과 일치."""
    raise NotImplementedError(
        "cold_start_job 본문은 A8 (recommendation) 에이전트 책임. "
        f"args: request_id={request_id} user_id={user_id} clusters={cluster_ids} "
        f"user_class={user_class} locale={locale}"
    )


__all__ = ["cold_start_job"]
