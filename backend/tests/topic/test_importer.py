"""CSO importer parse + BFS unit. DB 없이 in-memory 동작."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.topic.cso_importer import (
    PRED_LABEL,
    PRED_SUPER,
    _strip_brackets,
    _unescape_literal,
    parse_cso_csv,
)
from app.topic.mapping import (
    EXPECTED_CLUSTERS,
    assign_cluster_labels,
    missing_seeds,
    verify_cluster_coverage,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SMALL_CSO_CSV = FIXTURE_DIR / "small_cso.csv"


def test_strip_brackets_uri() -> None:
    assert _strip_brackets("<http://example.org/x>") == "http://example.org/x"


def test_strip_brackets_literal() -> None:
    assert _strip_brackets('"Hello World"') == "Hello World"


def test_unescape_literal_quote() -> None:
    assert _unescape_literal('escaped \\" quote') == 'escaped " quote'


def test_parse_cso_csv_fixture_loads_topics() -> None:
    """fixture CSV 가 모든 12 cluster seed 라벨을 포함하는지 검증."""
    topics = parse_cso_csv(SMALL_CSO_CSV)
    # 13 seed 토픽 + 추가 후손 = >= 13
    assert len(topics) > 13
    labels = {t["label"] for t in topics.values() if t.get("label")}
    assert "Artificial Intelligence" in labels
    assert "Hardware" in labels
    assert "Computer Graphics" in labels
    assert "Multimedia" in labels


def test_parse_cso_csv_parent_uris() -> None:
    """superTopicOf 의 자식 → 부모 매핑 검증.

    AI superTopicOf ML, ML superTopicOf NLP, AI superTopicOf NLP →
    ML.parent_uris = [AI], NLP.parent_uris = [AI, ML].
    """
    topics = parse_cso_csv(SMALL_CSO_CSV)
    ml = topics["http://cso.kmi.open.ac.uk/topics/machine_learning"]
    nlp = topics["http://cso.kmi.open.ac.uk/topics/natural_language_processing"]
    ai = "http://cso.kmi.open.ac.uk/topics/artificial_intelligence"
    ml_uri = "http://cso.kmi.open.ac.uk/topics/machine_learning"
    assert ai in ml["parent_uris"]
    # NLP 는 다중 부모 (AI + ML)
    assert ai in nlp["parent_uris"]
    assert ml_uri in nlp["parent_uris"]
    assert len(nlp["parent_uris"]) == 2


def test_parse_cso_csv_multiple_parent_neural_ir() -> None:
    """Neural IR 은 ML + IR 두 부모를 가짐."""
    topics = parse_cso_csv(SMALL_CSO_CSV)
    nir = topics["http://cso.kmi.open.ac.uk/topics/neural_ir"]
    parents = set(nir["parent_uris"])
    assert "http://cso.kmi.open.ac.uk/topics/machine_learning" in parents
    assert "http://cso.kmi.open.ac.uk/topics/information_retrieval" in parents


def test_assign_cluster_labels_with_fixture_covers_12_clusters() -> None:
    """fixture 의 12 cluster seed 가 모두 매칭."""
    topics = parse_cso_csv(SMALL_CSO_CSV)
    assert missing_seeds(topics) == []
    cluster_assignments = assign_cluster_labels(topics)
    missing = verify_cluster_coverage(cluster_assignments)
    assert missing == set(), f"Missing clusters: {missing}"


def test_assign_cluster_labels_multi_cluster_neural_ir() -> None:
    """Neural IR 은 AI + IR 두 cluster (다중 부모)."""
    topics = parse_cso_csv(SMALL_CSO_CSV)
    cluster_assignments = assign_cluster_labels(topics)
    nir_uri = "http://cso.kmi.open.ac.uk/topics/neural_ir"
    assert cluster_assignments[nir_uri] == {"AI", "IR"}


def test_assign_cluster_labels_graphics_multimedia_distinct_subtrees() -> None:
    """Computer Graphics 와 Multimedia 의 후손은 모두 Graphics·Multimedia cluster."""
    topics = parse_cso_csv(SMALL_CSO_CSV)
    cluster_assignments = assign_cluster_labels(topics)
    ray = "http://cso.kmi.open.ac.uk/topics/ray_tracing"
    audio = "http://cso.kmi.open.ac.uk/topics/audio_processing"
    assert cluster_assignments[ray] == {"Graphics·Multimedia"}
    assert cluster_assignments[audio] == {"Graphics·Multimedia"}


@pytest.mark.parametrize(
    "cluster_label",
    sorted(EXPECTED_CLUSTERS),
)
def test_each_cluster_has_at_least_one_node_in_fixture(
    cluster_label: str,
) -> None:
    """fixture 가 12 cluster 각각에 최소 1 노드 보유."""
    topics = parse_cso_csv(SMALL_CSO_CSV)
    cluster_assignments = assign_cluster_labels(topics)
    matched = [
        uri for uri, labels in cluster_assignments.items() if cluster_label in labels
    ]
    assert len(matched) >= 1, f"cluster {cluster_label} 에 매핑된 노드 0건"


def test_predicate_constants() -> None:
    """RDF predicate 상수 — 의도치 않은 변경 회귀 차단."""
    assert PRED_SUPER == "<http://cso.kmi.open.ac.uk/schema/cso#superTopicOf>"
    assert PRED_LABEL == "<http://www.w3.org/2000/01/rdf-schema#label>"
