"""SEEDS 12 cluster + BFS 라벨링 unit. DB·Redis 없이 동작."""
from __future__ import annotations

from app.topic.mapping import (
    EXPECTED_CLUSTERS,
    SEEDS,
    assign_cluster_labels,
    missing_seeds,
    verify_cluster_coverage,
)


def test_seeds_has_13_entries_12_unique_clusters() -> None:
    """SEEDS dict 는 13 entry (Computer Graphics + Multimedia 가 2 seed → 1 cluster).
    EXPECTED_CLUSTERS 는 12 unique label.
    """
    assert len(SEEDS) == 13
    assert len(EXPECTED_CLUSTERS) == 12
    # Graphics·Multimedia 가 2 seed
    graphics_count = sum(
        1 for v in SEEDS.values() if v == "Graphics·Multimedia"
    )
    assert graphics_count == 2


def test_assign_cluster_labels_basic() -> None:
    """간단한 4 노드 그래프 — AI seed → 2 후손, Hardware seed → 1 후손."""
    topics = {
        "uri://ai": {"label": "Artificial Intelligence", "parent_uris": [], "equivalents": []},
        "uri://ml": {"label": "Machine Learning", "parent_uris": ["uri://ai"], "equivalents": []},
        "uri://nlp": {"label": "Natural Language Processing", "parent_uris": ["uri://ml"], "equivalents": []},
        "uri://hw": {"label": "Hardware", "parent_uris": [], "equivalents": []},
        "uri://cpu": {"label": "CPU Design", "parent_uris": ["uri://hw"], "equivalents": []},
    }
    result = assign_cluster_labels(topics)
    assert result["uri://ai"] == {"AI"}
    assert result["uri://ml"] == {"AI"}
    assert result["uri://nlp"] == {"AI"}  # AI 의 후손이므로 AI cluster
    assert result["uri://hw"] == {"Hardware"}
    assert result["uri://cpu"] == {"Hardware"}


def test_assign_cluster_labels_multi_cluster_topic() -> None:
    """한 토픽이 두 cluster 후손 → set 으로 양쪽 모두 label."""
    topics = {
        "uri://ai": {"label": "Artificial Intelligence", "parent_uris": [], "equivalents": []},
        "uri://ir": {"label": "Information Retrieval", "parent_uris": [], "equivalents": []},
        # 두 seed 의 공동 후손
        "uri://nir": {
            "label": "Neural IR",
            "parent_uris": ["uri://ai", "uri://ir"],
            "equivalents": [],
        },
    }
    result = assign_cluster_labels(topics)
    assert result["uri://nir"] == {"AI", "IR"}


def test_assign_cluster_labels_graphics_multimedia_two_seeds() -> None:
    """Computer Graphics + Multimedia 두 seed 가 같은 Graphics·Multimedia cluster.

    각 seed 후손은 동일 cluster_label 부여.
    """
    topics = {
        "uri://cg": {"label": "Computer Graphics", "parent_uris": [], "equivalents": []},
        "uri://mm": {"label": "Multimedia", "parent_uris": [], "equivalents": []},
        "uri://ray": {"label": "Ray Tracing", "parent_uris": ["uri://cg"], "equivalents": []},
        "uri://av": {"label": "Audio Video", "parent_uris": ["uri://mm"], "equivalents": []},
    }
    result = assign_cluster_labels(topics)
    assert result["uri://cg"] == {"Graphics·Multimedia"}
    assert result["uri://mm"] == {"Graphics·Multimedia"}
    assert result["uri://ray"] == {"Graphics·Multimedia"}
    assert result["uri://av"] == {"Graphics·Multimedia"}


def test_assign_cluster_labels_case_insensitive() -> None:
    """label 매칭은 lowercase 정규화."""
    topics = {
        "uri://ai": {
            "label": "ARTIFICIAL INTELLIGENCE",  # 대문자
            "parent_uris": [],
            "equivalents": [],
        },
        "uri://ml": {
            "label": "Machine Learning",
            "parent_uris": ["uri://ai"],
            "equivalents": [],
        },
    }
    result = assign_cluster_labels(topics)
    assert result["uri://ai"] == {"AI"}
    assert result["uri://ml"] == {"AI"}


def test_missing_seeds_detects_absent_label() -> None:
    """seed 라벨 1개 누락 시 missing_seeds 가 보고."""
    topics = {
        "uri://ai": {"label": "Artificial Intelligence", "parent_uris": [], "equivalents": []},
        # 나머지 12 seed 부재
    }
    missing = missing_seeds(topics)
    assert "Artificial Intelligence" not in missing
    assert "Hardware" in missing
    assert "Theory of Computation" in missing
    # 12 seed 중 11 missing (AI 만 매치)
    assert len(missing) == 12


def test_verify_cluster_coverage_returns_missing() -> None:
    """EXPECTED_CLUSTERS 모두 cover 안 되면 누락 set 반환."""
    cluster_assignments = {
        "uri://ai": {"AI"},
        "uri://hw": {"Hardware"},
    }
    missing = verify_cluster_coverage(cluster_assignments)
    assert "Theory" in missing
    assert "Security" in missing
    assert "AI" not in missing
    assert "Hardware" not in missing


def test_verify_cluster_coverage_empty_when_all_covered() -> None:
    """모든 cluster 가 cover 되면 빈 set."""
    cluster_assignments = {
        f"uri://{cluster}": {cluster} for cluster in EXPECTED_CLUSTERS
    }
    assert verify_cluster_coverage(cluster_assignments) == set()
