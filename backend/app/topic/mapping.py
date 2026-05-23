"""CSO 12 cluster seed 매핑 + BFS 라벨링.

cso-mapping.md §12 클러스터 표 그대로 구현. CSO 원본 라벨 → cluster_label 매핑.
import 시 BFS 로 모든 후손 노드에 cluster_label set 부여. 한 토픽이 여러 cluster
의 후손이면 set 유지 (예: {"AI", "IR"}).

`Computer Graphics` + `Multimedia` 2 seed 가 동일 `Graphics·Multimedia` cluster
로 매핑. label 매칭은 `lower().strip()` 정규화로 대소문자·공백 변형 흡수.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

# CSO 12 cluster seed (cso-mapping.md §12 클러스터). lowercase 매칭 — label_to_uri 도 lower 정규화.
#
# (2026-05-16 fix) CSO 3.4.1 에는 다음 5 cluster 의 root 라벨이 명시적으로 없음.
# CSO 에 실재하는 가장 가까운 root 토픽으로 교체:
#   - "Computer Systems Organization" → "Operating Systems"
#   - "Theory of Computation"         → "Automata Theory"
#   - "Computer Graphics"             → "Interactive Computer Graphics"
#   - "Multimedia"                    → "Multimedia Systems"
#   - "Computational Science"         → "Scientific Computing"
# cluster_label 자체는 보존 (BroadInterest.cso_cluster_label 호환).
#
# (C-46, 2026-05-24) CSO 3.5 전환 시 본 5 cluster 라벨이 다시 추가됐는지 미검증.
# 시연 시 cluster 라벨 부재 (`Hardware` / `Theory` 등) 발견되면 본 SEEDS 와
# `backend/app/config/broad_interests.toml` 동시 교체. 일단 3.4.1 호환 라벨 유지.
SEEDS: dict[str, str] = {
    "Artificial Intelligence": "AI",
    "Operating Systems": "Systems",
    "Hardware": "Hardware",
    "Automata Theory": "Theory",
    "Software Engineering": "SE",
    "Computer Networks": "Networks",
    "Information Systems": "IS·DB",
    "Information Retrieval": "IR",
    "Security and Privacy": "Security",
    "Human-Computer Interaction": "HCI",
    "Interactive Computer Graphics": "Graphics·Multimedia",
    "Multimedia Systems": "Graphics·Multimedia",
    "Scientific Computing": "Computational Science",
}

# 12 unique cluster labels (Graphics·Multimedia 가 2 seed → 1 cluster). verify_cso_import 에서 사용.
EXPECTED_CLUSTERS: frozenset[str] = frozenset(SEEDS.values())


def _norm_label(label: str) -> str:
    """label 매칭 정규화 — lowercase + underscore → space + 다중 공백 collapse + strip.

    (2026-05-16 fix) CSO 3.4.1+ (현 3.5) 가 토픽 라벨을 snake_case (`artificial_intelligence`) 로
    저장 → 기존 lowercase+strip 만으로는 broad_interests.toml 의 `"Artificial Intelligence"`
    와 매칭 실패. underscore → space 변환 추가 + 다중 공백 정리로 양쪽 형태 모두 흡수.
    """
    import re

    s = label.lower().strip()
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def assign_cluster_labels(
    topics: Mapping[str, Mapping[str, Any]],
) -> dict[str, set[str]]:
    """12 seed 에서 BFS 로 후손에 cluster_label 부여.

    Args:
        topics: {uri: {"label": str | None, "parent_uris": list[str], ...}}.
            parent_uris 는 자식 → 부모 매핑 (`subTopicOf` 의 object).
    Returns:
        {uri: {cluster_label, ...}}. 매핑 안 된 URI 는 key 부재.
    """
    # 1. label → URI lookup (lowercase 정규화)
    label_to_uri: dict[str, str] = {}
    for uri, t in topics.items():
        label = t.get("label")
        if isinstance(label, str) and label:
            label_to_uri.setdefault(_norm_label(label), uri)

    # 2. seed label → seed URI (있는 것만 — 매칭 안 된 seed 는 warn 대상)
    seed_uris: dict[str, str] = {}
    for seed_label, cluster_label in SEEDS.items():
        norm = _norm_label(seed_label)
        if norm in label_to_uri:
            seed_uris[label_to_uri[norm]] = cluster_label

    # 3. BFS from each seed → 후손 모두 cluster_label 부여
    cluster_assignments: dict[str, set[str]] = defaultdict(set)
    # 자식 lookup 가속용 역인덱스 (parent_uri → list[child_uri])
    children_index: dict[str, list[str]] = defaultdict(list)
    for uri, t in topics.items():
        for p in t.get("parent_uris", []):
            children_index[p].append(uri)

    for seed_uri, cluster_label in seed_uris.items():
        queue: list[str] = [seed_uri]
        visited: set[str] = set()
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            cluster_assignments[current].add(cluster_label)
            queue.extend(children_index.get(current, []))

    return dict(cluster_assignments)


def missing_seeds(
    topics: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """매칭 실패한 SEEDS 라벨 list. Step 6 가드 — 누락 있으면 RuntimeError 발생용."""
    label_to_uri = {
        _norm_label(t["label"]): uri
        for uri, t in topics.items()
        if isinstance(t.get("label"), str) and t["label"]
    }
    return [
        seed_label
        for seed_label in SEEDS
        if _norm_label(seed_label) not in label_to_uri
    ]


def verify_cluster_coverage(cluster_assignments: Mapping[str, Iterable[str]]) -> set[str]:
    """배정된 cluster set 이 EXPECTED_CLUSTERS 와 일치하는지 검증.

    Returns:
        누락된 cluster set (정상 동작 시 set()).
    """
    seen: set[str] = set()
    for labels in cluster_assignments.values():
        seen.update(labels)
    return set(EXPECTED_CLUSTERS) - seen


__all__ = [
    "EXPECTED_CLUSTERS",
    "SEEDS",
    "assign_cluster_labels",
    "missing_seeds",
    "verify_cluster_coverage",
]
