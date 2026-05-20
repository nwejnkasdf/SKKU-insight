"""core / adjacent / discovery 후보 생성 SQL — recommendation-ranking.md §후보 생성.

각 slot SQL:
- core: (cso_topic_id IN current_csos OR leaf_topic_id IN current_leaves)
- adjacent: cso_topic_id IN adjacent_csos (current 제외)
- discovery: cso_topic_id IN (all CSO - trace_path_csos) AND Source.trust_level='high'

공통 AntiJoin 6종:
1. NOT IN SavedDocument
2. NOT IN HiddenDocument
3. NOT IN NotInterestedTopic (cso_id 또는 leaf_id)
4. NOT IN ClickbaitResult.decision='clickbait'
5. content_type != 'pseudo_cold_start' (cold-start pseudo 는 본인 만 사용, 일반 추천 제외)
6. leaf.status NOT IN ('merged','archived')  — A7 결정 #16

Anti-pattern §11.#3 방어: emerging quota race — leaf.status 컬럼을 후보 fetch 시점에
함께 가져옴 (LEFT JOIN dynamic_leaf_topic). emerging vs active 구분은 in-memory 후처리.
별도 SQL 호출 → A7 cron 의 status 전이 사이 race 차단.

returns list[CandidateRow] — DocumentTopic 다중 매핑 시 같은 document_id 가 여러 row 로
반환됨. ranking 단계에서 max(topic_match) dedup.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    and_,
    exists,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from app.db.models import (
    ClickbaitResult,
    CSOTopic,
    Document,
    DocumentTopic,
    DynamicLeafTopic,
    HiddenDocument,
    NotInterestedTopic,
    SavedDocument,
    Source,
)

# 후보 풀 size — slot 별 (ranking 전).
_CANDIDATE_LIMIT_PER_SLOT = 50


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """SQL 1 row — Document x DocumentTopic 매핑.

    같은 document 가 다중 (cso, leaf) 매핑 시 여러 row 로 반환됨. ranking dedup.
    """

    document_id: UUID
    title: str
    source_id: UUID
    source_name: str
    source_type: str   # contracts SourceType value
    trust_level: str
    published_at: datetime | None
    cso_topic_id: UUID | None
    leaf_topic_id: UUID | None
    leaf_status: str | None    # None | 'emerging' | 'active' (merged/archived 는 WHERE 에서 제외)
    leaf_label: str | None
    cso_label: str | None      # CSOTopic.label — reasons.py LLM input
    topic_confidence: float


def _antijoin_clauses(
    user_id: UUID, *, include_not_interested: bool = True
) -> list[ColumnElement[bool]]:
    """공통 AntiJoin 6종 WHERE clause list — Document.document_id 기준."""
    clauses: list[ColumnElement[bool]] = [
        # 1. NOT IN SavedDocument
        ~exists().where(
            SavedDocument.user_id == user_id,
            SavedDocument.document_id == Document.document_id,
        ),
        # 2. NOT IN HiddenDocument
        ~exists().where(
            HiddenDocument.user_id == user_id,
            HiddenDocument.document_id == Document.document_id,
        ),
        # 4. NOT IN ClickbaitResult.decision='clickbait'
        ~exists().where(
            ClickbaitResult.document_id == Document.document_id,
            ClickbaitResult.decision == "clickbait",
        ),
        # 5. content_type != 'pseudo_cold_start' (일반 추천 경로에서 제외)
        Document.content_type != "pseudo_cold_start",
    ]
    if include_not_interested:
        # 3. NOT IN NotInterestedTopic — cso_topic_id OR leaf_topic_id 양쪽.
        clauses.append(
            ~exists().where(
                NotInterestedTopic.user_id == user_id,
                or_(
                    and_(
                        NotInterestedTopic.cso_topic_id.is_not(None),
                        NotInterestedTopic.cso_topic_id == DocumentTopic.cso_topic_id,
                    ),
                    and_(
                        NotInterestedTopic.leaf_topic_id.is_not(None),
                        NotInterestedTopic.leaf_topic_id == DocumentTopic.leaf_topic_id,
                    ),
                ),
            )
        )
    return clauses


def _build_base_select() -> Select[Any]:
    """Document x DocumentTopic x Source x (LEFT JOIN) DynamicLeafTopic SELECT.

    LEFT JOIN dynamic_leaf_topic: cso-only 매핑 (leaf_topic_id IS NULL) 시 leaf_status=NULL.
    leaf 매핑 시 leaf.status 컬럼 반환 → ranking 단계에서 emerging vs active 구분.
    Source JOIN 으로 name / source_type / trust_level 함께 fetch.

    A7 결정 #16: merged/archived leaf 는 SELECT 자체에서 제외 (WHERE leaf.status IN (...)).
    """
    return (
        select(
            Document.document_id,
            Document.title,
            Document.source_id,
            Source.name.label("source_name"),
            Source.source_type.label("source_type"),
            Source.trust_level.label("trust_level"),
            Document.published_at,
            DocumentTopic.cso_topic_id,
            DocumentTopic.leaf_topic_id,
            DynamicLeafTopic.status.label("leaf_status"),
            DynamicLeafTopic.label.label("leaf_label"),
            CSOTopic.label.label("cso_label"),
            DocumentTopic.confidence.label("topic_confidence"),
        )
        .join(DocumentTopic, DocumentTopic.document_id == Document.document_id)
        .join(Source, Source.source_id == Document.source_id)
        .outerjoin(
            DynamicLeafTopic,
            DynamicLeafTopic.leaf_topic_id == DocumentTopic.leaf_topic_id,
        )
        .outerjoin(CSOTopic, CSOTopic.cso_topic_id == DocumentTopic.cso_topic_id)
    )


def _filter_leaf_status_valid() -> ColumnElement[bool]:
    """leaf.status NULL (cso-only) 또는 IN (emerging, active) — merged/archived 제외."""
    return or_(
        DynamicLeafTopic.leaf_topic_id.is_(None),
        DynamicLeafTopic.status.in_(["emerging", "active"]),
    )


def _row_to_candidate(row: Any) -> CandidateRow:
    """SQL row → CandidateRow dataclass."""
    return CandidateRow(
        document_id=row.document_id,
        title=row.title,
        source_id=row.source_id,
        source_name=row.source_name,
        source_type=row.source_type,
        trust_level=row.trust_level,
        published_at=row.published_at,
        cso_topic_id=row.cso_topic_id,
        leaf_topic_id=row.leaf_topic_id,
        leaf_status=row.leaf_status,
        leaf_label=row.leaf_label,
        cso_label=row.cso_label,
        topic_confidence=float(row.topic_confidence),
    )


async def query_core(
    db: AsyncSession,
    user_id: UUID,
    current_csos: list[UUID],
    current_leaves: list[UUID],
    *,
    limit: int = _CANDIDATE_LIMIT_PER_SLOT,
) -> list[CandidateRow]:
    """core slot 후보 — current_csos 또는 current_leaves 매핑.

    current_csos: active trace path 끝 노드 cso_topic_id list (A7 get_current_topics).
    current_leaves: active trace path 산하 active+emerging leaf_topic_id list.

    둘 다 비어 있으면 빈 list 반환 (cold-start 또는 새 사용자).
    """
    if not current_csos and not current_leaves:
        return []
    or_clauses: list[ColumnElement[bool]] = []
    if current_csos:
        or_clauses.append(DocumentTopic.cso_topic_id.in_(current_csos))
    if current_leaves:
        or_clauses.append(DocumentTopic.leaf_topic_id.in_(current_leaves))
    stmt = _build_base_select().where(
        or_(*or_clauses),
        _filter_leaf_status_valid(),
        # core 는 이미 active trace 로 확정된 현재 관심사라 not_interested topic 을
        # 절대 차단으로 쓰지 않는다. 해당 신호는 Bayesian score 하락으로만 반영한다.
        *_antijoin_clauses(user_id, include_not_interested=False),
    ).order_by(Document.published_at.desc().nulls_last()).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [_row_to_candidate(r) for r in rows]


async def query_adjacent(
    db: AsyncSession,
    user_id: UUID,
    adjacent_csos: list[UUID],
    current_csos: list[UUID],
    *,
    limit: int = _CANDIDATE_LIMIT_PER_SLOT,
) -> list[CandidateRow]:
    """adjacent slot 후보 — adjacent_csos 매핑 + current_csos 제외 (path 위 노드 자체는 adjacent X).

    adjacent_csos: A7 get_adjacent_topics 결과 (1-hop 이웃, current 제외 이미 적용됨).
    current_csos: 추가 안전 가드 (race 대응) — adjacent 가 current 와 겹치지 않도록.
    """
    if not adjacent_csos:
        return []
    extra_clauses: list[ColumnElement[bool]] = []
    if current_csos:
        # current 노드 자체 매핑은 제외 (race 대비 추가 안전 가드)
        extra_clauses.append(DocumentTopic.cso_topic_id.notin_(current_csos))
    stmt = _build_base_select().where(
        DocumentTopic.cso_topic_id.in_(adjacent_csos),
        *extra_clauses,
        _filter_leaf_status_valid(),
        *_antijoin_clauses(user_id),
    ).order_by(Document.published_at.desc().nulls_last()).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [_row_to_candidate(r) for r in rows]


async def query_discovery_trend(
    db: AsyncSession,
    user_id: UUID,
    excluded_trace_path_csos: list[UUID],
    *,
    limit: int = _CANDIDATE_LIMIT_PER_SLOT,
) -> list[CandidateRow]:
    """discovery slot fallback 룰 — 사용자 trace path 에 없는 trust=high trend.

    A8-v2 (2026-05-19) 이전 본 함수가 discovery slot 전부였음. 이제 fallback chain 의
    마지막 단계 — UserProfile 부재 또는 fusion/reincarnation/seeds 모두 비었을 때만.

    proactive 카테고리 (recommendation-ranking.md §Discovery FR-41 fallback).
    """
    base = _build_base_select().where(
        Source.trust_level == "high",
        _filter_leaf_status_valid(),
        *_antijoin_clauses(user_id),
    )
    if excluded_trace_path_csos:
        base = base.where(
            DocumentTopic.cso_topic_id.notin_(excluded_trace_path_csos)
        )
    stmt = base.order_by(Document.published_at.desc().nulls_last()).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [_row_to_candidate(r) for r in rows]


async def query_discovery_fusion(
    db: AsyncSession,
    user_id: UUID,
    bridge_cso_topic_id: UUID,
    *,
    limit: int = _CANDIDATE_LIMIT_PER_SLOT,
) -> list[CandidateRow]:
    """A9 discovery slot 1 (Fusion) — UserProfile.fusion_candidates[0].bridge_cso_topic_id
    로 SELECT. bridge 가 archive x current cross-product 의 새 영역.

    AntiJoin 6종 + leaf_status 가드 + freshness DESC. trust_level filter 없음
    (fusion 은 적합도 우선 — trust 는 ranking 의 w_trust=0.1 만).

    decisions.md §15 + algorithms/recommendation-ranking.md §Discovery.
    """
    stmt = (
        _build_base_select()
        .where(
            DocumentTopic.cso_topic_id == bridge_cso_topic_id,
            _filter_leaf_status_valid(),
            *_antijoin_clauses(user_id),
        )
        .order_by(Document.published_at.desc().nulls_last())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [_row_to_candidate(r) for r in rows]


async def query_discovery_reincarnation(
    db: AsyncSession,
    user_id: UUID,
    path_tail_cso_topic_id: UUID,
    archived_leaf_ids: list[UUID],
    *,
    limit: int = _CANDIDATE_LIMIT_PER_SLOT,
) -> list[CandidateRow]:
    """A9 discovery slot 2 (Reincarnation) — score_tail >= 0.6 archived trace 의 path
    끝 CSO + 산하 archived leaf 매핑 Document.

    `archived_leaf_ids` 는 traversal.get_descendant_archived_leaves 결과.
    가드: AntiJoin 6종 동일 + leaf_status 룰은 본 query 에서는 archived/merged 도 허용
    (`_filter_leaf_status_valid()` 와 다르게 archived leaf 도 후보) — Reincarnation 의
    핵심은 archive 부활이므로.

    decisions.md §15 + What Is Serendipity? (RecSys '25) "taste reincarnation".
    """
    leaf_filter: ColumnElement[bool]
    if archived_leaf_ids:
        leaf_filter = or_(
            DocumentTopic.cso_topic_id == path_tail_cso_topic_id,
            DocumentTopic.leaf_topic_id.in_(archived_leaf_ids),
        )
    else:
        leaf_filter = DocumentTopic.cso_topic_id == path_tail_cso_topic_id
    stmt = (
        _build_base_select()
        .where(
            leaf_filter,
            *_antijoin_clauses(user_id),
        )
        .order_by(Document.published_at.desc().nulls_last())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [_row_to_candidate(r) for r in rows]


# Backward-compat alias — 기존 caller (cold-start path) 가 호출 가능.
# A8-v2 본문 갱신으로 engine.build_dashboard 는 명시적으로 query_discovery_trend 호출하며,
# 본 alias 는 외부 import (예: tests) 호환만 유지.
async def query_discovery(
    db: AsyncSession,
    user_id: UUID,
    excluded_trace_path_csos: list[UUID],
    *,
    limit: int = _CANDIDATE_LIMIT_PER_SLOT,
) -> list[CandidateRow]:
    """Deprecated — `query_discovery_trend` 직접 호출 권장."""
    return await query_discovery_trend(
        db, user_id, excluded_trace_path_csos, limit=limit
    )


async def query_emerging_leaf_documents(
    db: AsyncSession,
    user_id: UUID,
    emerging_leaf_ids: list[UUID],
    *,
    limit: int = _CANDIDATE_LIMIT_PER_SLOT,
) -> list[CandidateRow]:
    """core emerging quota 후보 — emerging leaf 매핑 Document.

    quota 1 (recommendation.toml.core_slot_quota.emerging_leaf_quota_in_core).
    빈 list 시 quota 자동 active 회수 (fallback.py).
    """
    if not emerging_leaf_ids:
        return []
    stmt = _build_base_select().where(
        DocumentTopic.leaf_topic_id.in_(emerging_leaf_ids),
        DynamicLeafTopic.status == "emerging",
        *_antijoin_clauses(user_id),
    ).order_by(Document.published_at.desc().nulls_last()).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [_row_to_candidate(r) for r in rows]


__all__ = [
    "CandidateRow",
    "query_adjacent",
    "query_core",
    "query_discovery",  # deprecated alias
    "query_discovery_fusion",
    "query_discovery_reincarnation",
    "query_discovery_trend",
    "query_emerging_leaf_documents",
]
