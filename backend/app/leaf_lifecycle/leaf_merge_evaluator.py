"""주간 leaf 병합 평가 — LLM `evaluate_merges` 호출.

leaf-topic-lifecycle.md L104-131. trigger: MERGE_EVALUATION_CRON (월 03:00 UTC).
trace merge 와는 **별개** — trace 영역 운영이 아닌 leaf 자체의 의미 동등 통합.

룰 trigger: 사용자 active leaf 중 label_similarity ≥ 0.75 OR 문서 Jaccard ≥ 0.6 후보를
LLM 평가. LLM 응답: {merges: [{primary_leaf_id, merged_leaf_ids[], label_after_merge_ko,
label_after_merge_en, rationale}]}.

execute: primary 의 label 갱신 + merged_leaf 들 status='merged' +
merged_into_leaf_topic_id=primary.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from itertools import combinations
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import LeafTopicStatus
from app.db.models import (
    DocumentTopic,
    DynamicLeafTopic,
)
from app.leaf_lifecycle.protocol import MergeProposal
from app.leaf_lifecycle.strict_validation import label_similarity
from app.llm_provider.protocol import (
    ChatMessage,
    FixtureNotFound,
    LLMProvider,
    ProviderError,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_MERGE = """당신은 토픽 정리 어시스턴트다. 사용자의 동적 리프 토픽 중 의미상
동일하거나 매우 유사한 쌍/그룹을 식별하라.

[지시]
- 각 병합 그룹 JSON: {primary_leaf_id, merged_leaf_ids[], label_after_merge_ko,
  label_after_merge_en, rationale}.
- 라벨 의미 유사도 < {label_similarity_min} 이면 병합 안 함.
- 문서 Jaccard ≥ {jaccard_min} 또는 라벨 의미유사도 매우 높음만 병합.
- primary 는 가장 활성도 높은 leaf (last_signal_active_day 우선) 권장.
- 응답 JSON: {"merges": [...]}.
"""


async def _document_set(db: AsyncSession, leaf_id: UUID) -> set[UUID]:
    """leaf 의 매핑 Document id 집합 (Jaccard 계산용)."""
    stmt = select(DocumentTopic.document_id).where(
        DocumentTopic.leaf_topic_id == leaf_id
    )
    rows = await db.execute(stmt)
    return {row[0] for row in rows}


def _jaccard(a: set[UUID], b: set[UUID]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


async def find_merge_candidates_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> list[tuple[DynamicLeafTopic, DynamicLeafTopic, float, float]]:
    """룰 trigger 후보 추출 — label_similarity ≥ 0.75 OR Jaccard ≥ 0.6.

    return: [(leaf_a, leaf_b, label_sim, jaccard)] — LLM 호출 전 사전 압축.
    """
    settings = get_settings()
    stmt = (
        select(DynamicLeafTopic)
        .where(
            DynamicLeafTopic.user_id == user_id,
            DynamicLeafTopic.status == LeafTopicStatus.ACTIVE.value,
        )
        .limit(settings.LEAF_MERGE_MAX_PER_USER)
    )
    leaves = list((await db.execute(stmt)).scalars().all())
    candidates: list[tuple[DynamicLeafTopic, DynamicLeafTopic, float, float]] = []
    # Jaccard 계산은 비싸므로 label_similarity 먼저 필터.
    for a, b in combinations(leaves, 2):
        sim = label_similarity(a.label, b.label)
        if sim >= settings.LEAF_MERGE_LABEL_SIMILARITY_MIN:
            jacc = _jaccard(
                await _document_set(db, a.leaf_topic_id),
                await _document_set(db, b.leaf_topic_id),
            )
            candidates.append((a, b, sim, jacc))
            continue
        # label 유사도 낮으면 Jaccard 만 별도 검사.
        docs_a = await _document_set(db, a.leaf_topic_id)
        docs_b = await _document_set(db, b.leaf_topic_id)
        jacc = _jaccard(docs_a, docs_b)
        if jacc >= settings.LEAF_MERGE_JACCARD_MIN:
            candidates.append((a, b, sim, jacc))
    return candidates


async def evaluate_merges_for_user(
    db: AsyncSession,
    provider: LLMProvider,
    user_id: UUID,
) -> list[MergeProposal]:
    """주간 cron — 사용자별 LLM 호출 entry."""
    settings = get_settings()
    candidates = await find_merge_candidates_for_user(db, user_id)
    if not candidates:
        return []

    # LLM 호출 — 후보 list 통째로 전달.
    user_content_lines = ["[병합 후보]"]
    for a, b, sim, jacc in candidates[:20]:
        user_content_lines.append(
            f"- a={a.leaf_topic_id} '{a.label}' / b={b.leaf_topic_id} '{b.label}' "
            f"label_sim={sim:.2f} jaccard={jacc:.2f}"
        )
    user_content = "\n".join(user_content_lines)
    system = SYSTEM_PROMPT_MERGE.replace(
        "{label_similarity_min}", str(settings.LEAF_MERGE_LABEL_SIMILARITY_MIN)
    ).replace("{jaccard_min}", str(settings.LEAF_MERGE_JACCARD_MIN))
    try:
        response = await provider.complete(
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user_content),
            ],
            model_slot="high",
            response_format="json",
            user_id=str(user_id),
        )
    except FixtureNotFound:
        logger.warning("evaluate_merges fixture missing user=%s", user_id)
        return []
    except ProviderError as exc:
        logger.warning("evaluate_merges LLM error: %s", exc)
        return []

    parsed = response.parsed_json
    if parsed is None and response.text:
        try:
            parsed = json.loads(response.text)
        except (ValueError, json.JSONDecodeError):
            return []
    if not isinstance(parsed, dict):
        return []
    merges_raw = parsed.get("merges", [])
    if not isinstance(merges_raw, list):
        return []
    proposals: list[MergeProposal] = []
    for item in merges_raw:
        if not isinstance(item, dict):
            continue
        try:
            proposal = MergeProposal(
                primary_leaf_id=UUID(str(item.get("primary_leaf_id"))),
                merged_leaf_ids=[
                    UUID(str(x)) for x in item.get("merged_leaf_ids", [])
                ],
                label_after_merge_ko=str(item.get("label_after_merge_ko", "")),
                label_after_merge_en=str(item.get("label_after_merge_en", "")),
                rationale=str(item.get("rationale", "")),
            )
        except (ValueError, TypeError) as exc:
            logger.warning("evaluate_merges parse fail: %s", exc)
            continue
        proposals.append(proposal)
    return proposals


async def execute_merges(
    db: AsyncSession,
    user_id: UUID,
    proposals: list[MergeProposal],
) -> int:
    """proposals 적용 — primary 갱신 + merged status='merged' + merged_into 마킹.

    return: 변경된 leaf 수.
    """
    changed = 0
    now = datetime.now(UTC)
    for prop in proposals:
        # primary 갱신.
        await db.execute(
            update(DynamicLeafTopic)
            .where(
                DynamicLeafTopic.leaf_topic_id == prop.primary_leaf_id,
                DynamicLeafTopic.user_id == user_id,
            )
            .values(
                label=prop.label_after_merge_ko,
                label_en=prop.label_after_merge_en,
            )
        )
        changed += 1
        for merged_id in prop.merged_leaf_ids:
            stmt = (
                update(DynamicLeafTopic)
                .where(
                    DynamicLeafTopic.leaf_topic_id == merged_id,
                    DynamicLeafTopic.user_id == user_id,
                )
                .values(
                    status=LeafTopicStatus.MERGED.value,
                    merged_into_leaf_topic_id=prop.primary_leaf_id,
                )
                .returning(DynamicLeafTopic.leaf_topic_id)
            )
            result = await db.execute(stmt)
            if result.scalar_one_or_none() is not None:
                changed += 1
    _ = now
    return changed


__all__ = [
    "evaluate_merges_for_user",
    "execute_merges",
    "find_merge_candidates_for_user",
]
