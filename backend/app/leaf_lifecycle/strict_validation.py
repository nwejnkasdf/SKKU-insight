"""Strict 검증 (A7 결정 #19) — identify_emerging LLM 응답의 서버측 룰 검증.

4겹 검증:
1. confidence ≥ LEAF_EMERGING_CONFIDENCE_MIN (default 0.6)
2. supporting_document_ids 길이 ≥ LEAF_EMERGING_SUPPORTING_DOCUMENTS_MIN (default 3)
3. trace_anchor_required — cso_topic_ids 가 사용자 active trace path 위 노드 (또는 그래프
   1-hop 자식) 산하만. 위반 candidate 거부 + LLM 재호출 (retry cap=1, 결정 #15).
4. 기존 active leaf 라벨 의미유사도 < LEAF_EMERGING_LABEL_SIMILARITY_DEDUP (default 0.75) —
   Levenshtein 정규화 (decisions.md §3 임베딩 미사용).

위반 시 candidate 거부. 모두 거부 시 caller 가 보강된 prompt 로 재호출 (LLM identifier).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from uuid import UUID

import networkx as nx

from app.config import get_settings
from app.db.models import DynamicLeafTopic, UserCSOTraversal
from app.leaf_lifecycle.protocol import NewLeafCandidate
from app.topic.graph import get_children


@dataclass(slots=True, frozen=True)
class ValidationResult:
    """1 candidate 검증 결과."""

    candidate: NewLeafCandidate
    accepted: bool
    rejection_reason: str | None = None  # "confidence" / "supporting" / "anchor" / "label_dedup"


def normalize_label(label: str) -> str:
    """라벨 dedup 비교용 정규화 — lowercase + whitespace 단일화."""
    return " ".join(label.lower().split())


def label_similarity(a: str, b: str) -> float:
    """0~1 라벨 의미 유사도. difflib SequenceMatcher (Levenshtein 정규화 유사).

    임베딩 미사용 (decisions.md §3). 본 함수가 LEAF_MERGE_LABEL_SIMILARITY_MIN /
    LEAF_EMERGING_LABEL_SIMILARITY_DEDUP 비교 시 사용.
    """
    return difflib.SequenceMatcher(
        None, normalize_label(a), normalize_label(b)
    ).ratio()


def _build_anchor_cso_set(
    active_traces: list[UserCSOTraversal],
    graph: nx.DiGraph,
) -> set[UUID]:
    """trace_anchor_required 검증용 허용 cso_topic_id 집합.

    각 active trace 의 path 위 노드 + path 끝 노드의 그래프 1-hop 자식 (산하).
    "산하" = path 위 노드 OR 그 1-hop 직접 자식. leaf-topic-lifecycle.md L60.

    (Codex R1 Suggested 2) `find_descendants` (transitive) → `get_children` (1-hop only).
    transitive 자손까지 허용하면 깊은 하위 토픽도 anchor 통과 — 룰 의도와 다름.
    """
    allowed: set[UUID] = set()
    for trace in active_traces:
        for cso_id in trace.path:
            allowed.add(cso_id)
        if trace.path:
            tail = trace.path[-1]
            try:
                children = list(get_children(graph, tail))
            except Exception:
                children = []
            allowed.update(children)
    return allowed


def validate_candidates(
    candidates: list[NewLeafCandidate],
    *,
    active_traces: list[UserCSOTraversal],
    existing_active_leaves: list[DynamicLeafTopic],
    graph: nx.DiGraph,
) -> tuple[list[ValidationResult], list[UUID]]:
    """전체 candidate 검증. 4 룰 모두 통과 시 accepted=True.

    return:
    - results: ValidationResult list (각 candidate 의 accept/reject)
    - violating_anchor_cso_ids: trace_anchor 위반 cso_topic_id list (LLM 재호출 prompt 에 명시)
    """
    settings = get_settings()
    anchor_set = _build_anchor_cso_set(active_traces, graph)
    existing_labels = [lf.label for lf in existing_active_leaves]
    # (C-59, 2026-05-25) anchor_set 의 cso label list — 룰 5 (CSO 중복 dedup) 용.
    # cluster root + 1-hop 자식만 비교 (전체 14k 라벨 비교는 비용 큼).
    cso_labels_in_anchor: list[str] = []
    for cso_id in anchor_set:
        if cso_id in graph:
            attrs = graph.nodes[cso_id]
            label = attrs.get("label")
            if label:
                cso_labels_in_anchor.append(str(label))

    results: list[ValidationResult] = []
    violating: list[UUID] = []
    for cand in candidates:
        # 1. confidence 검증.
        if cand.confidence < settings.LEAF_EMERGING_CONFIDENCE_MIN:
            results.append(
                ValidationResult(cand, accepted=False, rejection_reason="confidence")
            )
            continue
        # 2. supporting documents 검증.
        if len(cand.supporting_document_ids) < settings.LEAF_EMERGING_SUPPORTING_DOCUMENTS_MIN:
            results.append(
                ValidationResult(cand, accepted=False, rejection_reason="supporting")
            )
            continue
        # 3. trace_anchor_required 검증.
        candidate_anchors = set(cand.cso_topic_ids)
        outside = candidate_anchors - anchor_set
        if outside:
            results.append(
                ValidationResult(cand, accepted=False, rejection_reason="anchor")
            )
            violating.extend(outside)
            continue
        # 4. 기존 active leaf 와 라벨 의미 유사도 dedup.
        max_sim = 0.0
        if existing_labels:
            max_sim = max(
                label_similarity(cand.label_ko, existing)
                for existing in existing_labels
            )
        if max_sim >= settings.LEAF_EMERGING_LABEL_SIMILARITY_DEDUP:
            results.append(
                ValidationResult(cand, accepted=False, rejection_reason="label_dedup")
            )
            continue
        # 5. (C-59, 2026-05-25) CSO 14k dedup — leaf 라벨이 anchor_set 의 cso label 과
        # 유사도 ≥ 임계면 reject. leaf = "CSO 에 없는 신생 토픽" 의도 정합 (이미 CSO 에
        # 있는 토픽은 leaf 만들 필요 X). label_ko + label_en 모두 비교.
        if cso_labels_in_anchor:
            cso_sim_max = max(
                max(
                    label_similarity(cand.label_ko, cso_label),
                    label_similarity(cand.label_en, cso_label),
                )
                for cso_label in cso_labels_in_anchor
            )
            if cso_sim_max >= settings.LEAF_EMERGING_CSO_DEDUP_THRESHOLD:
                results.append(
                    ValidationResult(cand, accepted=False, rejection_reason="cso_exists")
                )
                continue
        results.append(ValidationResult(cand, accepted=True))
    return results, violating


__all__ = [
    "ValidationResult",
    "label_similarity",
    "normalize_label",
    "validate_candidates",
]
