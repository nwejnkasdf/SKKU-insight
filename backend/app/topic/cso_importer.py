"""CSO CSV parse + INSERT 라이브러리. scripts/import_cso.py 가 호출.

cso-import.md §2 parse, §4 INSERT 의사 코드 그대로. idempotent — `ON CONFLICT
(uri) DO NOTHING` (cso_topic) + `ON CONFLICT DO NOTHING` (cso_topic_parent composite PK).

BroadInterest 12 행 시드: `backend/app/config/broad_interests.toml` 의 entry 를
`ON CONFLICT (name) DO UPDATE` 로 INSERT. `cso_seed_topic_id` 는 seed_topic_label
로 cso_topic FK resolve.
"""
from __future__ import annotations

import csv
import logging
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BroadInterest, CSOTopic, CSOTopicParent
from app.topic.mapping import _norm_label

logger = logging.getLogger(__name__)

# CSO RDF predicate (cso-import.md §2)
PRED_SUPER = "<http://cso.kmi.open.ac.uk/schema/cso#superTopicOf>"
PRED_LABEL = "<http://www.w3.org/2000/01/rdf-schema#label>"
PRED_PREF = "<http://cso.kmi.open.ac.uk/schema/cso#preferentialEquivalent>"
PRED_REL = "<http://cso.kmi.open.ac.uk/schema/cso#relatedEquivalent>"

# 배치 크기 — 14k 노드를 1000 chunk 14 batch 로 분할. 시간 초과 시 500/200 으로 조정.
INSERT_BATCH_SIZE = 1000


def _strip_brackets(value: str) -> str:
    """<http://...> 또는 "literal" → 내부 값. CSO CSV 의 RDF triple 표기."""
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        return value[1:-1]
    if value.startswith('"'):
        # "label"@en 또는 "literal"^^<xsd:string> — 첫 닫는 따옴표까지
        end = value.rfind('"')
        if end > 0:
            return value[1:end]
    return value


def _unescape_literal(value: str) -> str:
    """RDF literal escape (`\\"`, `\\\\`) 복원. label 텍스트 정규화."""
    return value.replace('\\"', '"').replace("\\\\", "\\")


def download_cso(url: str, cache_dir: Path, refresh: bool = False) -> Path:
    """CSO CSV 다운로드 → `.cache/cso/CSO.X.Y.csv` 캐시.

    Args:
        url: CSO_DOWNLOAD_URL env (예: https://cso.kmi.open.ac.uk/downloads/CSO.3.4.csv)
        cache_dir: 보통 워크트리 루트의 `.cache/cso/`
        refresh: True 면 캐시 무시 + 재다운로드
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / url.rsplit("/", 1)[-1]
    if target.exists() and not refresh:
        logger.info("CSO cache hit: %s", target)
        return target
    logger.info("CSO download: %s → %s", url, target)
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as r:
        r.raise_for_status()
        with target.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return target


def parse_cso_csv(path: Path) -> dict[str, dict[str, Any]]:
    """CSO CSV → {uri: {"label": str | None, "parent_uris": [...], "equivalents": [...]}}.

    superTopicOf 의 subject 는 부모, object 는 자식 — child[parent_uris].append(subject).
    """
    topics: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3:
                continue
            s_raw, p_raw, o_raw = row[0], row[1], row[2]
            s_uri = _strip_brackets(s_raw)
            o_val = _strip_brackets(o_raw)
            p = p_raw.strip()
            t = topics.setdefault(
                s_uri, {"label": None, "parent_uris": [], "equivalents": []}
            )
            if p == PRED_LABEL:
                t["label"] = _unescape_literal(o_val)
            elif p == PRED_SUPER:
                # subject = 부모, object = 자식
                child = topics.setdefault(
                    o_val, {"label": None, "parent_uris": [], "equivalents": []}
                )
                child["parent_uris"].append(s_uri)
            elif p == PRED_REL:
                t["equivalents"].append(o_val)
            # PRED_PREF: 1차 미사용
    logger.info("CSO parse: %d topics", len(topics))
    return topics


async def insert_cso(
    session: AsyncSession,
    topics: Mapping[str, Mapping[str, Any]],
    cluster_assignments: Mapping[str, set[str]],
) -> dict[str, UUID]:
    """2-pass INSERT (idempotent).

    Pass 1: label + URI + cluster_labels → ON CONFLICT (uri) DO NOTHING.
            이미 존재하는 URI 는 기존 id SELECT.
    Pass 2 (a): cso_topic.parent_topic_id 채움 (BFS 첫 부모, parent URI sort 결정성).
    Pass 2 (b): cso_topic_parent M:N — 모든 부모 ON CONFLICT DO NOTHING.

    Returns:
        uri → cso_topic_id 매핑. BroadInterest 시드에서 사용.
    """
    uri_to_id: dict[str, UUID] = {}

    # Pass 1: 노드 INSERT (chunked)
    uris = list(topics.keys())
    for i in range(0, len(uris), INSERT_BATCH_SIZE):
        chunk_uris = uris[i : i + INSERT_BATCH_SIZE]
        values = [
            {
                "label": (
                    topics[uri]["label"]
                    or uri.rsplit("/", 1)[-1].replace("_", " ")
                ),
                "uri": uri,
                "parent_topic_id": None,
                "cluster_labels": list(cluster_assignments.get(uri, set())),
            }
            for uri in chunk_uris
        ]
        stmt = (
            pg_insert(CSOTopic)
            .values(values)
            .on_conflict_do_nothing(index_elements=["uri"])
        )
        await session.execute(stmt)
    await session.flush()

    # 모든 uri → id 조회 (이미 존재 + 신규 모두 포함)
    rows = await session.execute(select(CSOTopic.cso_topic_id, CSOTopic.uri))
    for row in rows:
        if row.uri in topics:
            uri_to_id[row.uri] = row.cso_topic_id

    # Pass 2 (a): parent_topic_id 채움 + (b) cso_topic_parent M:N
    parent_pairs: list[dict[str, UUID]] = []
    primary_updates: list[dict[str, Any]] = []
    for uri, t in topics.items():
        parents = t.get("parent_uris", [])
        if not parents:
            continue
        # parent URI sort (결정성)
        sorted_parents = sorted(parents)
        primary = sorted_parents[0]
        child_id = uri_to_id.get(uri)
        primary_id = uri_to_id.get(primary)
        if child_id is None:
            continue
        if primary_id is not None:
            primary_updates.append({"id": child_id, "parent": primary_id})
        for p in sorted_parents:
            p_id = uri_to_id.get(p)
            if p_id is None:
                continue
            parent_pairs.append(
                {"cso_topic_id": child_id, "parent_cso_topic_id": p_id}
            )

    # primary parent UPDATE — bulk
    if primary_updates:
        for i in range(0, len(primary_updates), INSERT_BATCH_SIZE):
            chunk = primary_updates[i : i + INSERT_BATCH_SIZE]
            for item in chunk:
                await session.execute(
                    update(CSOTopic)
                    .where(CSOTopic.cso_topic_id == item["id"])
                    .values(parent_topic_id=item["parent"])
                )
        await session.flush()

    # cso_topic_parent M:N — ON CONFLICT DO NOTHING (composite PK)
    if parent_pairs:
        for i in range(0, len(parent_pairs), INSERT_BATCH_SIZE):
            chunk = parent_pairs[i : i + INSERT_BATCH_SIZE]
            stmt2 = pg_insert(CSOTopicParent).values(chunk).on_conflict_do_nothing()
            await session.execute(stmt2)
        await session.flush()

    logger.info(
        "CSO insert: nodes=%d primary_parents=%d m2n_pairs=%d",
        len(uri_to_id),
        len(primary_updates),
        len(parent_pairs),
    )
    return uri_to_id


async def seed_broad_interests(
    session: AsyncSession,
    toml_path: Path,
    uri_to_id: Mapping[str, UUID],
    topics: Mapping[str, Mapping[str, Any]],
) -> int:
    """BroadInterest 12 행 시드. `backend/app/config/broad_interests.toml` SOR.

    각 entry 형식:
        [[broad_interest]]
        name = "AI"
        description_ko = "..."
        cso_cluster_label = "AI"
        seed_topic_label = "Artificial Intelligence"   # CSO 원본 라벨
        display_order = 0
    """
    with toml_path.open("rb") as f:
        config = tomllib.load(f)

    entries = config.get("broad_interest", [])
    if not entries:
        raise RuntimeError(f"{toml_path} 에 [[broad_interest]] entry 없음")

    # label → uri 역인덱스 (정규화)
    label_to_uri: dict[str, str] = {}
    for uri, t in topics.items():
        label = t.get("label")
        if isinstance(label, str) and label:
            label_to_uri.setdefault(_norm_label(label), uri)

    inserted = 0
    for entry in entries:
        seed_label = entry["seed_topic_label"]
        norm = _norm_label(seed_label)
        seed_uri = label_to_uri.get(norm)
        if seed_uri is None:
            logger.warning(
                "BroadInterest seed_topic_label 매칭 실패: %s — skip", seed_label
            )
            continue
        seed_id = uri_to_id.get(seed_uri)
        if seed_id is None:
            logger.warning("seed URI 가 INSERT 단계에 없음: %s — skip", seed_uri)
            continue
        stmt = (
            pg_insert(BroadInterest)
            .values(
                name=entry["name"],
                description=entry["description_ko"],
                cso_cluster_label=entry["cso_cluster_label"],
                cso_seed_topic_id=seed_id,
                display_order=entry.get("display_order", 0),
            )
            .on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "description": entry["description_ko"],
                    "cso_cluster_label": entry["cso_cluster_label"],
                    "cso_seed_topic_id": seed_id,
                    "display_order": entry.get("display_order", 0),
                },
            )
        )
        await session.execute(stmt)
        inserted += 1
    await session.flush()
    logger.info("BroadInterest seed: %d/%d", inserted, len(entries))
    return inserted


async def reset_cso_tables(session: AsyncSession) -> None:
    """--reset 플래그용. broad_interest → cso_topic_parent → cso_topic 순 TRUNCATE.

    FK RESTRICT (broad_interest.cso_seed_topic_id) 때문에 순서 중요. CASCADE 한 번에:
    `TRUNCATE cso_topic CASCADE` 가 broad_interest·cso_topic_parent·dynamic_leaf_topic_cso_topic
    까지 비움 — 그러나 RESTRICT 는 CASCADE 도 거부함. 명시 순서로 DELETE.
    """
    logger.info("--reset: DELETE broad_interest → cso_topic_parent → cso_topic")
    await session.execute(text("DELETE FROM broad_interest"))
    await session.execute(text("DELETE FROM cso_topic_parent"))
    await session.execute(text("DELETE FROM cso_topic"))
    await session.flush()


__all__ = [
    "INSERT_BATCH_SIZE",
    "PRED_LABEL",
    "PRED_PREF",
    "PRED_REL",
    "PRED_SUPER",
    "download_cso",
    "insert_cso",
    "parse_cso_csv",
    "reset_cso_tables",
    "seed_broad_interests",
]
