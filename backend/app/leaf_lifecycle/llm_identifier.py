"""Identify Emerging LLM 호출 + Strict 검증 + trace_anchor 위반 재호출.

A7 결정 매트릭스 #14/#15/#18/#19:
- trigger: collection daily cron 직후 hook (worker/jobs/leaf_lifecycle.py)
- input D: A4 user own collection union UserEvent click/save (최근 24h)
- 검증: confidence/supporting/anchor/label_dedup 4 룰 (strict_validation.py)
- anchor 위반 시 보강된 prompt 로 즉시 1회 재호출 (LEAF_LLM_ANCHOR_RETRY_CAP=1)

LLM prompt 골격: leaf-topic-lifecycle.md L65-102. Mock fixture 5 종 중
identify_emerging fixture 가 본 prompt hash 매칭.
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

import networkx as nx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from sqlalchemy import select

from app.db.models import Document, DynamicLeafTopic, UserCSOTraversal
from app.leaf_lifecycle.protocol import NewLeafCandidate
from app.leaf_lifecycle.strict_validation import (
    ValidationResult,
    validate_candidates,
)
from app.llm_provider.protocol import (
    ChatMessage,
    FixtureNotFound,
    LLMProvider,
    ProviderError,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_IDENTIFY = """당신은 학술/기술 큐레이션 어시스턴트다.
사용자가 최근 24시간 동안 인터랙션 또는 수집한 문서들을 살펴보고, 기존 동적 리프 토픽에
속하지 않으며 새로 부상하는 세부 주제 후보를 식별하라.

[지시]
- 최대 {max_new_leaves_per_day}개 candidate.
- 각 candidate JSON: {label_ko, label_en, cso_topic_ids[], supporting_document_ids[],
  confidence: float, rationale}.
- confidence < 0.6 자동 제외 (서버측 검증과 정합).
- supporting_document_ids 길이 ≥ 3.
- cso_topic_ids 는 반드시 사용자 active trace path 위 노드 또는 그 그래프 1-hop 자손 (산하)
  만 포함 (trace_anchor_required=true). 위반 시 candidate 거부됨.
- 빈 candidate list 도 가능 (식별할 만한 신규 주제 없음 = 자연스러운 응답).

응답은 JSON: {"candidates": [...]}.
"""


def _build_user_prompt(
    new_documents: list[tuple[UUID, str, str]],
    existing_leaves: list[DynamicLeafTopic],
    active_traces: list[UserCSOTraversal],
    *,
    anchor_violations: list[UUID] | None = None,
) -> str:
    """LLM user content. anchor_violations 가 있으면 재호출 시 보강 안내 추가.

    new_documents 는 (document_id, title, summary) 튜플 — LLM 이 본문 보고 후보를
    만들 수 있게 메타데이터 동봉.
    """
    parts: list[str] = []
    parts.append(f"[기존 active leaf list ({len(existing_leaves)})]")
    for leaf in existing_leaves[:50]:
        parts.append(f"- {leaf.label} (id={leaf.leaf_topic_id})")
    parts.append(f"[active trace path list ({len(active_traces)})]")
    for trace in active_traces[:10]:
        parts.append(
            f"- trace={trace.trace_id} path_len={len(trace.path)} "
            f"path={[str(p) for p in trace.path]}"
        )
    parts.append(f"[input documents (최근 24h, {len(new_documents)} 건)]")
    for doc_id, title, summary in new_documents[:30]:
        snippet = (summary or "")[:240].replace("\n", " ")
        parts.append(f"- id={doc_id} | {title} | {snippet}")
    if anchor_violations:
        parts.append(
            f"\n[ANCHOR 위반 재호출 — 이전 응답의 다음 cso_topic_id 들은 "
            f"trace path 산하 외였습니다. 새 응답에서 사용 금지: "
            f"{[str(v) for v in anchor_violations]}]"
        )
    return "\n".join(parts)


async def _fetch_document_summaries(
    db: AsyncSession, document_ids: list[UUID]
) -> list[tuple[UUID, str, str]]:
    """document_id list → (id, title, summary) 튜플 list. 순서는 입력 보존."""
    if not document_ids:
        return []
    rows = (
        await db.execute(
            select(Document.document_id, Document.title, Document.summary).where(
                Document.document_id.in_(document_ids)
            )
        )
    ).all()
    by_id = {row.document_id: (row.document_id, row.title or "", row.summary or "") for row in rows}
    return [by_id[d] for d in document_ids if d in by_id]


async def _call_llm_identify(
    db: AsyncSession,
    provider: LLMProvider,
    user_id: UUID,
    new_documents: list[UUID],
    existing_leaves: list[DynamicLeafTopic],
    active_traces: list[UserCSOTraversal],
    *,
    anchor_violations: list[UUID] | None = None,
) -> list[NewLeafCandidate]:
    """LLM `identify_emerging` 호출 → parse → NewLeafCandidate list.

    실패 (FixtureNotFound / ProviderError / JSON parse 실패) 시 빈 list 반환 + warning.
    """
    settings = get_settings()
    system = SYSTEM_PROMPT_IDENTIFY.replace(
        "{max_new_leaves_per_day}", str(settings.LEAF_EMERGING_MAX_PER_DAY)
    )
    document_summaries = await _fetch_document_summaries(db, new_documents)
    user_content = _build_user_prompt(
        document_summaries,
        existing_leaves,
        active_traces,
        anchor_violations=anchor_violations,
    )
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
    except FixtureNotFound as exc:
        logger.warning(
            "identify_emerging fixture missing for user=%s hash=%s", user_id, exc
        )
        return []
    except ProviderError as exc:
        logger.warning("identify_emerging LLM error: %s", exc)
        return []

    parsed = response.parsed_json
    if parsed is None and response.text:
        try:
            parsed = json.loads(response.text)
        except (ValueError, json.JSONDecodeError):
            logger.warning("identify_emerging JSON parse fail user=%s", user_id)
            return []
    if not isinstance(parsed, dict):
        return []
    raw_candidates = parsed.get("candidates", [])
    if not isinstance(raw_candidates, list):
        return []

    results: list[NewLeafCandidate] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        try:
            cand = NewLeafCandidate(
                label_ko=str(item.get("label_ko", "")),
                label_en=str(item.get("label_en", "")),
                cso_topic_ids=[
                    UUID(str(x)) for x in item.get("cso_topic_ids", [])
                ],
                supporting_document_ids=[
                    UUID(str(x)) for x in item.get("supporting_document_ids", [])
                ],
                confidence=float(item.get("confidence", 0.0)),
                rationale=str(item.get("rationale", "")),
            )
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("identify_emerging candidate parse fail: %s", exc)
            continue
        results.append(cand)
    return results


async def identify_emerging_with_validation(
    db: AsyncSession,
    provider: LLMProvider,
    graph: nx.DiGraph,
    user_id: UUID,
    new_documents: list[UUID],
    existing_leaves: list[DynamicLeafTopic],
    active_traces: list[UserCSOTraversal],
) -> list[ValidationResult]:
    """Identify Emerging 의 entry point — 본 함수만 caller 가 호출.

    1차 LLM 호출 → Strict 검증 → anchor 위반 candidate 있으면 retry (cap=1) →
    2차 검증 → 결과 반환. accepted=True candidate 만 DB INSERT 가능.

    return: ValidationResult list. caller 가 accepted=True 만 골라 INSERT.
    """
    settings = get_settings()
    # 1차 호출.
    candidates = await _call_llm_identify(
        db, provider, user_id, new_documents, existing_leaves, active_traces
    )
    if not candidates:
        return []
    results, violating = validate_candidates(
        candidates,
        active_traces=active_traces,
        existing_active_leaves=existing_leaves,
        graph=graph,
    )
    # 모두 accept 거나, anchor 위반 외 사유로만 거부됐으면 종료.
    has_anchor_violation = any(r.rejection_reason == "anchor" for r in results)
    if not has_anchor_violation or settings.LEAF_LLM_ANCHOR_RETRY_CAP <= 0:
        return results
    # anchor 위반 발생 — retry cap=1 으로 재호출.
    retry_cap = settings.LEAF_LLM_ANCHOR_RETRY_CAP
    for _ in range(retry_cap):
        retry_candidates = await _call_llm_identify(
            db,
            provider,
            user_id,
            new_documents,
            existing_leaves,
            active_traces,
            anchor_violations=violating,
        )
        if not retry_candidates:
            break
        retry_results, retry_violating = validate_candidates(
            retry_candidates,
            active_traces=active_traces,
            existing_active_leaves=existing_leaves,
            graph=graph,
        )
        # 재호출 결과로 results 교체 (anchor 위반 candidate 들이 사라지면 OK).
        results = retry_results
        violating = retry_violating
        if not any(r.rejection_reason == "anchor" for r in results):
            break
    return results


__all__ = [
    "SYSTEM_PROMPT_IDENTIFY",
    "identify_emerging_with_validation",
]
