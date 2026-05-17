"""Strict 검증 4 룰 매트릭스 (A7 결정 #19).

- confidence < 0.6 → rejection_reason='confidence'
- supporting_documents < 3 → 'supporting'
- trace_anchor_required 위반 (path 위 노드 또는 그래프 1-hop 자손 외) → 'anchor'
- 기존 active leaf 라벨 의미유사도 >= 0.75 → 'label_dedup'
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import networkx as nx

from app.contracts import LeafTopicStatus, TraversalStatus
from app.leaf_lifecycle.protocol import NewLeafCandidate
from app.leaf_lifecycle.strict_validation import (
    label_similarity,
    normalize_label,
    validate_candidates,
)


def _build_graph(edges: list[tuple[uuid.UUID, uuid.UUID]]) -> nx.DiGraph:
    """edges = list of (parent, child). graph 컨벤션 (`child → parent` edge) 정합 —
    add_edge(child, parent) 로 등록 (graph.py: successors=부모 / find_descendants=nx.ancestors)."""
    g: nx.DiGraph = nx.DiGraph()
    for parent, child in edges:
        g.add_edge(child, parent)
    return g


def _mock_trace(path: list[uuid.UUID]) -> MagicMock:
    trace = MagicMock()
    trace.trace_id = uuid.uuid4()
    trace.user_id = uuid.uuid4()
    trace.path = path
    trace.status = TraversalStatus.ACTIVE.value
    return trace


def _mock_leaf(label: str) -> MagicMock:
    leaf = MagicMock()
    leaf.leaf_topic_id = uuid.uuid4()
    leaf.label = label
    leaf.status = LeafTopicStatus.ACTIVE.value
    return leaf


def _candidate(
    *,
    cso_ids: list[uuid.UUID],
    confidence: float = 0.8,
    docs: int = 5,
    label_ko: str = "신규 토픽",
) -> NewLeafCandidate:
    return NewLeafCandidate(
        label_ko=label_ko,
        label_en="New Topic",
        cso_topic_ids=cso_ids,
        supporting_document_ids=[uuid.uuid4() for _ in range(docs)],
        confidence=confidence,
        rationale="test",
    )


class TestNormalizeAndSimilarity:
    def test_normalize_lowercase_and_whitespace(self) -> None:
        assert normalize_label("  Hello   World  ") == "hello world"

    def test_similarity_identical(self) -> None:
        assert label_similarity("RAG", "rag") == 1.0

    def test_similarity_disjoint(self) -> None:
        assert label_similarity("RAG", "Quantum Computing") < 0.5

    def test_similarity_close_strings(self) -> None:
        # "RAG Retrieval" vs "RAG Retreival" (오타)
        sim = label_similarity("RAG Retrieval", "RAG Retreival")
        assert 0.7 <= sim <= 1.0


class TestValidateCandidates:
    def _setup(self):
        """active path = [root, mid] + 자손 child. 자손 산하만 허용."""
        root = uuid.uuid4()
        mid = uuid.uuid4()
        child = uuid.uuid4()
        outsider = uuid.uuid4()
        graph = _build_graph(
            [
                (root, mid),
                (mid, child),
                # outsider 는 path 밖 분리된 노드.
            ]
        )
        graph.add_node(outsider)
        trace = _mock_trace(path=[root, mid])
        return graph, trace, root, mid, child, outsider

    def test_accept_when_inside_anchor(self) -> None:
        graph, trace, _root, _mid, child, _ = self._setup()
        cand = _candidate(cso_ids=[child])  # mid 의 1-hop 자손
        results, _ = validate_candidates(
            [cand],
            active_traces=[trace],
            existing_active_leaves=[],
            graph=graph,
        )
        assert len(results) == 1
        assert results[0].accepted is True

    def test_accept_when_on_path(self) -> None:
        graph, trace, root, _mid, _, _ = self._setup()
        cand = _candidate(cso_ids=[root])  # path 위 root 자체
        results, _ = validate_candidates(
            [cand],
            active_traces=[trace],
            existing_active_leaves=[],
            graph=graph,
        )
        assert results[0].accepted is True

    def test_reject_anchor_outside(self) -> None:
        graph, trace, _, _, _, outsider = self._setup()
        cand = _candidate(cso_ids=[outsider])
        results, violating = validate_candidates(
            [cand],
            active_traces=[trace],
            existing_active_leaves=[],
            graph=graph,
        )
        assert results[0].accepted is False
        assert results[0].rejection_reason == "anchor"
        assert outsider in violating

    def test_reject_confidence_below_threshold(self) -> None:
        graph, trace, _, _, child, _ = self._setup()
        cand = _candidate(cso_ids=[child], confidence=0.55)
        results, _ = validate_candidates(
            [cand],
            active_traces=[trace],
            existing_active_leaves=[],
            graph=graph,
        )
        assert results[0].accepted is False
        assert results[0].rejection_reason == "confidence"

    def test_reject_supporting_below_min(self) -> None:
        graph, trace, _, _, child, _ = self._setup()
        cand = _candidate(cso_ids=[child], docs=2)  # min 3
        results, _ = validate_candidates(
            [cand],
            active_traces=[trace],
            existing_active_leaves=[],
            graph=graph,
        )
        assert results[0].accepted is False
        assert results[0].rejection_reason == "supporting"

    def test_reject_label_dedup_with_existing(self) -> None:
        graph, trace, _, _, child, _ = self._setup()
        existing = _mock_leaf("RAG")
        cand = _candidate(cso_ids=[child], label_ko="RAG")  # 동일 label
        results, _ = validate_candidates(
            [cand],
            active_traces=[trace],
            existing_active_leaves=[existing],
            graph=graph,
        )
        assert results[0].accepted is False
        assert results[0].rejection_reason == "label_dedup"

    def test_violating_list_includes_outsiders_only(self) -> None:
        """다중 candidate, 일부 accept + 일부 anchor 위반."""
        graph, trace, _, _, child, outsider = self._setup()
        cand_good = _candidate(cso_ids=[child], label_ko="새 토픽 A")
        cand_bad = _candidate(cso_ids=[outsider], label_ko="새 토픽 B")
        results, violating = validate_candidates(
            [cand_good, cand_bad],
            active_traces=[trace],
            existing_active_leaves=[],
            graph=graph,
        )
        assert results[0].accepted is True
        assert results[1].accepted is False
        assert violating == [outsider]
