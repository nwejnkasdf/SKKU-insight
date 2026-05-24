"""다양성 룰 — recommendation-ranking.md §다양성 룰.

slot 별 greedy diversification:
- 동일 source_id ≤ max_per_source_in_slot (default 2)
- 동일 leaf_topic_id ≤ max_per_leaf_in_slot (default 3, leaf=None 면 cap 면제)

입력은 ranking 결과 (score DESC 정렬). 출력은 cap 통과 후 동일 정렬.
"""
from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from .config_loader import DiversificationConfig
from .ranking import ScoredCandidate

_LLM_SEARCH_SENTINEL_NAME = "llm_search"


def diversify(
    scored: list[ScoredCandidate],
    cfg: DiversificationConfig,
) -> list[ScoredCandidate]:
    """greedy cap — source / leaf 별.

    leaf_topic_id is None 인 후보는 leaf cap 면제 (cso-only 매핑).
    source cap 은 실제 source 에 적용한다. v13 수집 구조의 `llm_search`
    sentinel 은 publisher 를 Document.raw 에 담는 공용 source 이므로 cap 적용 시
    정상 후보가 슬롯당 2개로 잘리는 문제가 있어 예외 처리한다.
    """
    selected: list[ScoredCandidate] = []
    src_count: dict[UUID, int] = defaultdict(int)
    leaf_count: dict[UUID, int] = defaultdict(int)
    for c in scored:
        applies_source_cap = c.source_name != _LLM_SEARCH_SENTINEL_NAME
        if applies_source_cap and src_count[c.source_id] >= cfg.max_per_source_in_slot:
            continue
        if c.leaf_topic_id is not None and (
            leaf_count[c.leaf_topic_id] >= cfg.max_per_leaf_in_slot
        ):
            continue
        selected.append(c)
        if applies_source_cap:
            src_count[c.source_id] += 1
        if c.leaf_topic_id is not None:
            leaf_count[c.leaf_topic_id] += 1
    return selected


__all__ = ["diversify"]
