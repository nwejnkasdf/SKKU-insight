"""Trace merge evaluator — A7 신규 operation (decisions.md §12 결정 #17/#21/#22/#23).

daily 18 UTC cron 이 본 모듈의 `find_merge_candidates_for_user` + `verify_and_execute`
를 호출. 룰 + LLM 결합 — 룰로 후보 압축 + LLM 으로 의미 검증.

룰 trigger (결정 #21):
- 두 active trace path 가 같은 cso_topic_id ≥ TRACE_MERGE_PATH_OVERLAP_MIN (=3) 공유, OR
- 한 path 가 다른 path 의 proper subset.

LLM 검증 (결정 #21):
- prompt: 두 trace 의 path label + 산하 leaf list + 활동도.
- LLM 응답: {decision: "merge"|"reject", winner: source_trace_id|other_trace_id, rationale}.

Winner 결정 (결정 #22):
- 기본: max(last_activity_active_day). tie 시 trace_id 더 작은 쪽 (deterministic).
- LLM 이 명시한 winner 가 있으면 우선 (양 trace 의 의미 보존을 위해 LLM 의도 존중).

Execute (결정 #22):
- winner.path 유지 + loser.status='archived' + loser.merged_into_trace_id=winner_id.
- loser 산하 leaf 재매핑.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import UserCSOTraversal
from app.llm_provider.protocol import (
    ChatMessage,
    FixtureNotFound,
    LLMProvider,
    ProviderError,
)
from app.traversal.operations import execute_merge
from app.traversal.protocol import MergePlan
from app.traversal.queries import get_active_traces, get_descendant_leaves

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class MergeCandidate:
    """룰 trigger 가 찾은 후보 (LLM 검증 전)."""

    source_trace_id: UUID
    other_trace_id: UUID
    overlap_count: int
    reason: str  # "path_overlap" | "proper_subset"


def find_merge_candidates(
    traces: list[UserCSOTraversal],
    overlap_min: int,
) -> list[MergeCandidate]:
    """active trace list 에서 룰 trigger 후보 추출.

    O(N^2) — 사용자당 active cap=10 이라 충분. 정렬:
    더 활동도 낮은 trace 가 loser 후보가 되도록 source/other 결정은 evaluator 가 함.
    """
    candidates: list[MergeCandidate] = []
    for t1, t2 in combinations(traces, 2):
        path1 = set(t1.path)
        path2 = set(t2.path)
        overlap = path1 & path2
        if len(overlap) >= overlap_min:
            candidates.append(
                MergeCandidate(
                    source_trace_id=t1.trace_id,
                    other_trace_id=t2.trace_id,
                    overlap_count=len(overlap),
                    reason="path_overlap",
                )
            )
        elif path1 < path2 or path2 < path1:
            # proper subset (한쪽이 다른쪽의 부분집합, 동등 X).
            candidates.append(
                MergeCandidate(
                    source_trace_id=t1.trace_id,
                    other_trace_id=t2.trace_id,
                    overlap_count=min(len(path1), len(path2)),
                    reason="proper_subset",
                )
            )
    return candidates


def _decide_winner(
    t1: UserCSOTraversal,
    t2: UserCSOTraversal,
) -> tuple[UUID, UUID]:
    """A7 결정 #22: winner = max(last_activity_active_day), tie 시 trace_id 작은 쪽.

    return: (winner_id, loser_id).
    """
    if t1.last_activity_active_day > t2.last_activity_active_day:
        return t1.trace_id, t2.trace_id
    if t1.last_activity_active_day < t2.last_activity_active_day:
        return t2.trace_id, t1.trace_id
    # tie — trace_id 더 작은 쪽 winner.
    if t1.trace_id < t2.trace_id:
        return t1.trace_id, t2.trace_id
    return t2.trace_id, t1.trace_id


async def _llm_verify_merge(
    provider: LLMProvider,
    db: AsyncSession,
    user_id: UUID,
    t1: UserCSOTraversal,
    t2: UserCSOTraversal,
) -> tuple[bool, str]:
    """LLM `trace_merge_verify` 호출 — 두 trace 가 의미상 merge 가능한지 검증.

    prompt: 각 trace 의 path label (cso_topic_id 만 — label resolution 은 worker 에서)
    + 산하 leaf labels + 활동도 (last_activity_active_day).
    return: (decision_merge, rationale).
    """
    # leaf labels 수집 (양 trace 합집합).
    leaves1 = await get_descendant_leaves(db, user_id, trace=t1)
    leaves2 = await get_descendant_leaves(db, user_id, trace=t2)

    system_prompt = (
        "당신은 토픽 정리 어시스턴트다. 두 사용자 traversal trace 가 의미상 동일한 관심 영역을 "
        "다루는지 판단하라. 같은 영역이면 merge 권장, 아니면 reject. JSON 응답: "
        '{"decision": "merge"|"reject", "rationale": "<한국어 한 문장>"}'
    )
    user_content = (
        f"trace_a: path_len={len(t1.path)} last_active_day={t1.last_activity_active_day} "
        f"leaves={[lf.label for lf in leaves1[:5]]}\n"
        f"trace_b: path_len={len(t2.path)} last_active_day={t2.last_activity_active_day} "
        f"leaves={[lf.label for lf in leaves2[:5]]}\n"
        "두 trace 가 의미상 동일 관심 영역인지 JSON 으로 응답하라."
    )
    try:
        response = await provider.complete(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_content),
            ],
            model_slot="high",
            response_format="json",
            user_id=str(user_id),
        )
    except FixtureNotFound:
        logger.warning(
            "trace_merge_verify fixture missing — defaulting to reject "
            "(user=%s t1=%s t2=%s)",
            user_id,
            t1.trace_id,
            t2.trace_id,
        )
        return False, "fixture_missing"
    except ProviderError as exc:
        logger.warning("trace_merge_verify LLM error: %s", exc)
        return False, f"llm_error:{exc}"

    parsed = response.parsed_json
    if not isinstance(parsed, dict):
        return False, "llm_parse_failed"
    decision = str(parsed.get("decision", "")).lower()
    rationale = str(parsed.get("rationale", ""))
    return decision == "merge", rationale


async def evaluate_and_execute_merges(
    db: AsyncSession,
    provider: LLMProvider,
    user_id: UUID,
    active_day_counter: int,
) -> list[MergePlan]:
    """daily cron 의 사용자별 entry point.

    1. active trace list 조회.
    2. 룰 trigger 후보 추출 (overlap ≥3 또는 proper subset).
    3. 후보별 LLM `trace_merge_verify` 호출.
    4. merge 결정 시 winner/loser 결정 → execute_merge.

    return: 실제 실행된 MergePlan list.
    """
    settings = get_settings()
    traces = await get_active_traces(db, user_id)
    if len(traces) < 2:
        return []
    candidates = find_merge_candidates(traces, settings.TRACE_MERGE_PATH_OVERLAP_MIN)
    if not candidates:
        return []

    trace_by_id: dict[UUID, UserCSOTraversal] = {t.trace_id: t for t in traces}
    executed: list[MergePlan] = []
    # 이미 archive 된 (다른 후보의 loser 가 된) trace 는 재처리 안 함.
    archived_ids: set[UUID] = set()
    for cand in candidates:
        if cand.source_trace_id in archived_ids or cand.other_trace_id in archived_ids:
            continue
        t1 = trace_by_id.get(cand.source_trace_id)
        t2 = trace_by_id.get(cand.other_trace_id)
        if t1 is None or t2 is None:
            continue
        do_merge, _ = await _llm_verify_merge(provider, db, user_id, t1, t2)
        if not do_merge:
            continue
        winner_id, loser_id = _decide_winner(t1, t2)
        # loser 산하 leaf 수집.
        loser_trace = trace_by_id[loser_id]
        loser_leaves = await get_descendant_leaves(db, user_id, trace=loser_trace)
        plan = MergePlan(
            winner_trace_id=winner_id,
            loser_trace_id=loser_id,
            leaves_to_reassign=[lf.leaf_topic_id for lf in loser_leaves],
        )
        await execute_merge(db, plan, user_id, active_day_counter)
        executed.append(plan)
        archived_ids.add(loser_id)
    return executed


__all__ = [
    "MergeCandidate",
    "evaluate_and_execute_merges",
    "find_merge_candidates",
]
