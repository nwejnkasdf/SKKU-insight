"""build_dashboard 메인 흐름 — recommendation-ranking.md §의사 코드 + sdd/data-flow §3.

is_cold_start 분기 → cold_start 경로 / 정상 경로:
  candidates (3 slot SQL + emerging_pool)
  → ranking (slot 별)
  → diversify (slot 별)
  → fill_slots (FR-42 + emerging quota)
  → FR-43 trend fallback (if total < 10)
  → materialize_cards (Document + TopicChip)
  → generate_reasons (LLM 1회 batch)
  → persist (Recommendation + RecommendationSlot)

§11.#1 방어 (cache-before-commit): build 자체는 db.commit() 호출 안 함 — caller
(service.get_dashboard) 가 결과 받은 후 commit → redis.setex 순서 수행.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import networkx as nx
import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.contracts import (
    SlotType,
    TopicChip,
)
from app.db.models import (
    CSOTopic,
    Document,
    DocumentTopic,
    DynamicLeafTopic,
    HiddenDocument,
    Recommendation,
    RecommendationSlot,
    SavedDocument,
    Source,
    User,
    UserInterestState,
)
from app.interest.config_loader import InterestParams
from app.llm_provider.protocol import LLMProvider
from app.traversal import queries as trav_queries

from .candidates import (
    query_adjacent,
    query_core,
    query_discovery,
    query_emerging_leaf_documents,
)
from .config_loader import RecommendationConfig
from .diversify import diversify
from .fallback import FilledSlots, build_trend_fallback, fill_slots
from .ranking import ScoredCandidate, StateKey, score_candidates
from .reasons import generate_reasons
from .schemas import (
    DashboardResponse,
    RecommendationCard,
    SlotSummary,
)

logger = logging.getLogger(__name__)


class ColdStartInProgress(Exception):
    """cold-start 진행 중 — caller 가 HTTPException 202 변환."""


@dataclass(slots=True)
class DashboardBuildResult:
    """build_dashboard 결과 — response + persisted row info."""

    response: DashboardResponse


async def _is_cold_start(db: AsyncSession, user: User) -> bool:
    """active trace 0개 AND UserInterestState 행동 신호 0 (alpha_prior 만)."""
    active_count = await trav_queries.count_active_traces(db, user.user_id)
    if active_count > 0:
        return False
    # boost_applied_at_active_day 가 있는 row 는 onboarding boost — 행동 신호 X.
    # 행동 신호 = boost 없이 long_alpha 가 prior 보다 큰 row.
    stmt = select(func.count(UserInterestState.state_id)).where(
        UserInterestState.user_id == user.user_id,
        UserInterestState.boost_applied_at_active_day.is_(None),
        # alpha 가 prior 보다 1.0 이상 큰 row 만 — view event 등 작은 신호는 제외.
        UserInterestState.long_alpha > 2.0,
    )
    behavioral_count = (await db.execute(stmt)).scalar_one()
    return behavioral_count == 0


async def _load_cold_start_dashboard(
    db: AsyncSession, user: User
) -> DashboardResponse:
    """이미 cold-start 완료된 사용자 — 저장된 Recommendation rows 를 serialize."""
    today_recs = await _select_today_recommendations(db, user.user_id)
    if not today_recs:
        today_recs = await _select_latest_recommendations(db, user.user_id)
    cards = await _materialize_cards(db, today_recs, user.user_id)
    slot_summaries = _serialize_slot_summaries_from_recs(today_recs)
    return DashboardResponse(
        user_id=user.user_id,
        cards=cards,
        slots=slot_summaries,
        generated_at=datetime.now(UTC),
        cache="miss",
        cold_start=True,
    )


async def _select_today_recommendations(
    db: AsyncSession, user_id: UUID
) -> list[Recommendation]:
    """오늘 일자 (UTC) Recommendation rows — slot_type 별 + created_at DESC."""
    stmt = (
        select(Recommendation)
        .where(Recommendation.user_id == user_id)
        .where(
            func.date(func.timezone("UTC", Recommendation.created_at))
            == func.date(func.timezone("UTC", func.now()))
        )
        .order_by(Recommendation.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def _select_latest_recommendations(
    db: AsyncSession, user_id: UUID
) -> list[Recommendation]:
    """UTC today rows 가 없으면 가장 최근 생성일의 Recommendation rows 를 복원."""
    latest_created = (
        await db.execute(
            select(func.max(Recommendation.created_at)).where(
                Recommendation.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if latest_created is None:
        return []
    if latest_created.tzinfo is None:
        latest_utc = latest_created.replace(tzinfo=UTC)
    else:
        latest_utc = latest_created.astimezone(UTC)
    day_start = datetime(
        latest_utc.year, latest_utc.month, latest_utc.day, tzinfo=UTC
    )
    day_end = day_start + timedelta(days=1)
    stmt = (
        select(Recommendation)
        .where(
            Recommendation.user_id == user_id,
            Recommendation.created_at >= day_start,
            Recommendation.created_at < day_end,
        )
        .order_by(Recommendation.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def _fetch_state_index(
    db: AsyncSession, user_id: UUID, limit: int = 200
) -> dict[StateKey, UserInterestState]:
    """UserInterestState → (cso_topic_id, leaf_topic_id) tuple key dict.

    bucket_for() 가 lookup. limit 200 = 자주 호출되는 토픽 만 (NFR-12 latency).
    """
    stmt = (
        select(UserInterestState)
        .where(UserInterestState.user_id == user_id)
        .order_by(UserInterestState.long_score.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {(r.cso_topic_id, r.leaf_topic_id): r for r in rows}


async def _fetch_current_leaves(
    db: AsyncSession, user_id: UUID
) -> list[UUID]:
    """모든 active trace 의 path 산하 (cso_topic_ids 매핑) active+emerging leaf id list."""
    traces = await trav_queries.get_active_traces(db, user_id)
    if not traces:
        return []
    seen: set[UUID] = set()
    result: list[UUID] = []
    for tr in traces:
        leaves = await trav_queries.get_descendant_leaves(
            db, user_id, trace=tr
        )
        for lf in leaves:
            if lf.leaf_topic_id in seen:
                continue
            seen.add(lf.leaf_topic_id)
            result.append(lf.leaf_topic_id)
    return result


async def _materialize_cards(
    db: AsyncSession,
    rows: list[Recommendation],
    user_id: UUID,
) -> list[RecommendationCard]:
    """Recommendation rows → RecommendationCard list. Document/Source/TopicChip JOIN.

    NFR-04: score 컬럼은 응답 schema 에 부재 — 명시 매핑만 (no **row).
    """
    if not rows:
        return []
    doc_ids = list({r.document_id for r in rows})
    # Document + Source fetch.
    doc_stmt = (
        select(
            Document.document_id,
            Document.title,
            Document.published_at,
            Source.name.label("source_name"),
            Source.source_type.label("source_type"),
        )
        .join(Source, Source.source_id == Document.source_id)
        .where(Document.document_id.in_(doc_ids))
    )
    doc_map: dict[UUID, dict[str, Any]] = {}
    for doc_row in await db.execute(doc_stmt):
        doc_map[doc_row.document_id] = {
            "title": doc_row.title,
            "published_at": doc_row.published_at,
            "source_name": doc_row.source_name,
            "source_type": doc_row.source_type,
        }
    # TopicChip fetch — document 별 모든 (cso/leaf) 매핑.
    chip_map = await _fetch_topic_chips(db, doc_ids)
    saved_set = await _fetch_saved_documents(db, user_id, doc_ids)
    hidden_set = await _fetch_hidden_documents(db, user_id, doc_ids)

    cards: list[RecommendationCard] = []
    for rec in rows:
        meta = doc_map.get(rec.document_id)
        if meta is None:
            continue
        published_meta = meta["published_at"]
        published_dt = published_meta if isinstance(published_meta, datetime) else datetime.now(UTC)
        cards.append(
            RecommendationCard(
                recommendation_id=rec.recommendation_id,
                document_id=rec.document_id,
                slot_type=SlotType(rec.slot_type),
                title=str(meta["title"]),
                source_name=str(meta["source_name"]),
                source_type=str(meta["source_type"]),
                related_topics=chip_map.get(rec.document_id, []),
                reason_short=rec.reason or "",
                published_at=published_dt,
                thumbnail_url=None,
            )
        )
    # saved/hidden flag 는 DocumentDetail 응답용 — dashboard card schema 에 부재.
    _ = saved_set, hidden_set
    return cards


async def _fetch_topic_chips(
    db: AsyncSession, document_ids: list[UUID]
) -> dict[UUID, list[TopicChip]]:
    """document_id 별 모든 (cso, leaf) 매핑 → TopicChip list.

    각 카드당 chip 최대 5개로 cap (cso + leaf 합).
    """
    if not document_ids:
        return {}
    stmt = (
        select(
            DocumentTopic.document_id,
            DocumentTopic.cso_topic_id,
            DocumentTopic.leaf_topic_id,
            CSOTopic.label.label("cso_label"),
            DynamicLeafTopic.label.label("leaf_label"),
        )
        .outerjoin(CSOTopic, CSOTopic.cso_topic_id == DocumentTopic.cso_topic_id)
        .outerjoin(
            DynamicLeafTopic,
            DynamicLeafTopic.leaf_topic_id == DocumentTopic.leaf_topic_id,
        )
        .where(DocumentTopic.document_id.in_(document_ids))
    )
    rows = (await db.execute(stmt)).all()
    chips: dict[UUID, list[TopicChip]] = {}
    # (self-review R1 fix) 같은 (topic_id, type) chip 중복 차단 — 같은 doc 가 같은 leaf
    # 에 여러 confidence 로 매핑되거나, partial UNIQUE 3종이 동일 (cso, leaf) 행 보유 시.
    seen_per_doc: dict[UUID, set[tuple[UUID, str]]] = {}
    for r in rows:
        doc_id: UUID = r.document_id
        bucket = chips.setdefault(doc_id, [])
        seen = seen_per_doc.setdefault(doc_id, set())
        if len(bucket) >= 5:
            continue
        chip: TopicChip | None = None
        if r.leaf_topic_id is not None and r.leaf_label:
            chip = TopicChip(
                topic_id=r.leaf_topic_id,
                label=str(r.leaf_label),
                type="leaf",
            )
        elif r.cso_topic_id is not None and r.cso_label:
            chip = TopicChip(
                topic_id=r.cso_topic_id,
                label=str(r.cso_label),
                type="cso",
            )
        if chip is None:
            continue
        key = (chip.topic_id, chip.type)
        if key in seen:
            continue
        seen.add(key)
        bucket.append(chip)
    return chips


async def _fetch_saved_documents(
    db: AsyncSession, user_id: UUID, document_ids: list[UUID]
) -> set[UUID]:
    if not document_ids:
        return set()
    stmt = select(SavedDocument.document_id).where(
        SavedDocument.user_id == user_id,
        SavedDocument.document_id.in_(document_ids),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return set(rows)


async def _fetch_hidden_documents(
    db: AsyncSession, user_id: UUID, document_ids: list[UUID]
) -> set[UUID]:
    if not document_ids:
        return set()
    stmt = select(HiddenDocument.document_id).where(
        HiddenDocument.user_id == user_id,
        HiddenDocument.document_id.in_(document_ids),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return set(rows)


def _serialize_slot_summaries(filled: FilledSlots, *, total_target: int = 10) -> list[SlotSummary]:
    """FilledSlots → SlotSummary list (target/actual/fallback_reason)."""
    targets = {
        SlotType.CORE: 5,
        SlotType.ADJACENT: 3,
        SlotType.DISCOVERY: 2,
    }
    summaries: list[SlotSummary] = []
    for slot in (SlotType.CORE, SlotType.ADJACENT, SlotType.DISCOVERY):
        bucket = getattr(filled, slot.value)
        summaries.append(
            SlotSummary(
                slot_type=slot,
                target_count=targets[slot],
                actual_count=len(bucket),
                fallback_reason=filled.fallback_reasons.get(slot),
            )
        )
    if filled.fallback_trend:
        summaries.append(
            SlotSummary(
                slot_type=SlotType.FALLBACK_TREND,
                target_count=0,
                actual_count=len(filled.fallback_trend),
                fallback_reason=filled.fallback_reasons.get(
                    SlotType.FALLBACK_TREND, "overall_short"
                ),
            )
        )
    return summaries


def _serialize_slot_summaries_from_recs(
    rows: list[Recommendation],
) -> list[SlotSummary]:
    """저장된 Recommendation row 들로부터 SlotSummary 복원 (cold-start cached path)."""
    targets = {
        SlotType.CORE: 5,
        SlotType.ADJACENT: 3,
        SlotType.DISCOVERY: 2,
    }
    counts: dict[SlotType, int] = {}
    for r in rows:
        try:
            slot = SlotType(r.slot_type)
        except ValueError:
            continue
        counts[slot] = counts.get(slot, 0) + 1
    summaries: list[SlotSummary] = []
    for slot in (SlotType.CORE, SlotType.ADJACENT, SlotType.DISCOVERY):
        summaries.append(
            SlotSummary(
                slot_type=slot,
                target_count=targets.get(slot, 0),
                actual_count=counts.get(slot, 0),
                fallback_reason=None,
            )
        )
    if SlotType.FALLBACK_TREND in counts:
        summaries.append(
            SlotSummary(
                slot_type=SlotType.FALLBACK_TREND,
                target_count=0,
                actual_count=counts[SlotType.FALLBACK_TREND],
                fallback_reason="overall_short",
            )
        )
    return summaries


async def _persist_recommendations(
    db: AsyncSession,
    user_id: UUID,
    filled: FilledSlots,
    reasons: dict[UUID, str],
) -> dict[UUID, UUID]:
    """Recommendation + RecommendationSlot rows INSERT.

    §11.#2 방어: daily UNIQUE race — `pg_insert(...).on_conflict_do_nothing()` 패턴.
    같은 (user, doc, slot, date) 가 이미 있으면 skip (refresh fallback 경로 안전).

    반환: dict[document_id, recommendation_id] — caller (engine) 가 카드의
    recommendation_id 매핑. on_conflict 시 기존 row id lookup.
    """
    bucket_iter: list[tuple[SlotType, list[ScoredCandidate]]] = [
        (SlotType.CORE, filled.core),
        (SlotType.ADJACENT, filled.adjacent),
        (SlotType.DISCOVERY, filled.discovery),
        (SlotType.FALLBACK_TREND, filled.fallback_trend),
    ]
    doc_to_rec_id: dict[UUID, UUID] = {}
    for slot, cards in bucket_iter:
        for c in cards:
            new_id = uuid4()
            reason = reasons.get(c.document_id, "")
            stmt = (
                pg_insert(Recommendation)
                .values(
                    recommendation_id=new_id,
                    user_id=user_id,
                    document_id=c.document_id,
                    slot_type=slot.value,
                    reason=reason[:255] if reason else None,
                    score=c.score,
                )
                .on_conflict_do_nothing()
                .returning(Recommendation.recommendation_id)
            )
            result = await db.execute(stmt)
            inserted = result.scalar_one_or_none()
            if inserted is not None:
                doc_to_rec_id[c.document_id] = inserted
            else:
                # race — 기존 row lookup
                lookup = (
                    await db.execute(
                        select(Recommendation.recommendation_id)
                        .where(
                            Recommendation.user_id == user_id,
                            Recommendation.document_id == c.document_id,
                            Recommendation.slot_type == slot.value,
                            func.date(
                                func.timezone("UTC", Recommendation.created_at)
                            )
                            == func.date(func.timezone("UTC", func.now())),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if lookup is not None:
                    doc_to_rec_id[c.document_id] = lookup
    # RecommendationSlot rows — slot 별 1 row.
    targets = {
        SlotType.CORE: 5,
        SlotType.ADJACENT: 3,
        SlotType.DISCOVERY: 2,
    }
    for slot in (SlotType.CORE, SlotType.ADJACENT, SlotType.DISCOVERY):
        bucket = getattr(filled, slot.value)
        db.add(
            RecommendationSlot(
                slot_id=uuid4(),
                user_id=user_id,
                slot_type=slot.value,
                target_count=targets[slot],
                actual_count=len(bucket),
                fallback_reason=filled.fallback_reasons.get(slot),
            )
        )
    if filled.fallback_trend:
        db.add(
            RecommendationSlot(
                slot_id=uuid4(),
                user_id=user_id,
                slot_type=SlotType.FALLBACK_TREND.value,
                target_count=0,
                actual_count=len(filled.fallback_trend),
                fallback_reason=filled.fallback_reasons.get(
                    SlotType.FALLBACK_TREND, "overall_short"
                ),
            )
        )
    return doc_to_rec_id


def _filled_slots_to_cards(
    filled: FilledSlots,
    doc_to_rec_id: dict[UUID, UUID],
    reasons: dict[UUID, str],
    *,
    chips: dict[UUID, list[TopicChip]],
) -> list[RecommendationCard]:
    """FilledSlots → RecommendationCard list (NFR-04 score 마스킹).

    명시 field 매핑만 — `**row` 사용 안 함 (§11.#4 방어).
    """
    out: list[RecommendationCard] = []
    bucket_iter: list[tuple[SlotType, list[ScoredCandidate]]] = [
        (SlotType.CORE, filled.core),
        (SlotType.ADJACENT, filled.adjacent),
        (SlotType.DISCOVERY, filled.discovery),
        (SlotType.FALLBACK_TREND, filled.fallback_trend),
    ]
    for slot, cards in bucket_iter:
        for c in cards:
            rec_id = doc_to_rec_id.get(c.document_id)
            if rec_id is None:
                continue
            out.append(
                RecommendationCard(
                    recommendation_id=rec_id,
                    document_id=c.document_id,
                    slot_type=slot,
                    title=c.title,
                    source_name=c.source_name,
                    source_type=c.source_type,
                    related_topics=chips.get(c.document_id, []),
                    reason_short=reasons.get(c.document_id, ""),
                    published_at=c.published_at or datetime.now(UTC),
                    thumbnail_url=None,
                )
            )
    return out


async def build_dashboard(
    db: AsyncSession,
    redis: aioredis.Redis,
    provider: LLMProvider,
    cso_graph: nx.DiGraph,
    settings: Settings,
    params: InterestParams,
    config: RecommendationConfig,
    *,
    user: User,
) -> DashboardBuildResult:
    """메인 build 흐름 — 정상 경로 + cold-start 분기.

    cold-start 진행 중 → ColdStartInProgress raise (caller 가 202 변환).
    cold-start 완료 → stored Recommendation rows serialize.
    정상 경로 → 후보 → ranking → diversify → fill → fallback → reasons → persist.

    DB commit 은 caller (service.get_dashboard) 가 책임 (§11.#1 cache-before-commit 회피).
    """
    if await _is_cold_start(db, user):
        return DashboardBuildResult(response=await _load_cold_start_dashboard(db, user))

    # 1. 후보 query base.
    current_csos = await trav_queries.get_current_topics(db, user.user_id)
    adjacent_csos = await trav_queries.get_adjacent_topics(
        db, cso_graph, user.user_id, hops=1
    )
    current_leaves = await _fetch_current_leaves(db, user.user_id)
    trace_path_csos: set[UUID] = set(current_csos)
    for trace in await trav_queries.get_active_traces(db, user.user_id):
        trace_path_csos.update(trace.path)
    emerging_leaves_orm = await trav_queries.get_emerging_leaves(db, user.user_id)
    emerging_leaf_ids = [lf.leaf_topic_id for lf in emerging_leaves_orm]
    state_index = await _fetch_state_index(db, user.user_id)

    # 2. SQL 3 slot + emerging.
    core_pool_raw = await query_core(
        db, user.user_id, current_csos, current_leaves
    )
    adjacent_pool_raw = await query_adjacent(
        db, user.user_id, adjacent_csos, current_csos
    )
    discovery_pool_raw = await query_discovery(
        db, user.user_id, list(trace_path_csos)
    )
    emerging_pool_raw = await query_emerging_leaf_documents(
        db, user.user_id, emerging_leaf_ids
    )

    # 3. ranking (slot 별).
    core_pool = score_candidates(
        core_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness,
        config.trust_level_weights,
        config.bucket_score,
    )
    adjacent_pool = score_candidates(
        adjacent_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness,
        config.trust_level_weights,
        config.bucket_score,
    )
    discovery_pool = score_candidates(
        discovery_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness,
        config.trust_level_weights,
        config.bucket_score,
    )
    emerging_pool = score_candidates(
        emerging_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness,
        config.trust_level_weights,
        config.bucket_score,
    )

    # 4. diversify (slot 별).
    core_pool = diversify(core_pool, config.diversification)
    adjacent_pool = diversify(adjacent_pool, config.diversification)
    discovery_pool = diversify(discovery_pool, config.diversification)
    emerging_pool = diversify(emerging_pool, config.diversification)

    # 5. fill_slots — emerging quota + threshold + FR-42 fallback.
    filled = fill_slots(
        core_pool=core_pool,
        adjacent_pool=adjacent_pool,
        discovery_pool=discovery_pool,
        emerging_pool=emerging_pool,
        targets=config.slot_targets,
        thresholds=config.confidence_thresholds,
        quota=config.core_slot_quota,
    )

    # 6. FR-43 — 전체 < 10 시 trend fallback.
    total = filled.total()
    if total < config.slot_targets.total:
        deficit = config.slot_targets.total - total
        excluded = filled.all_document_ids()
        trend_raw = await build_trend_fallback(
            db, user.user_id, excluded, deficit, cfg=config.fallback
        )
        trend_scored = score_candidates(
            trend_raw,
            state_index,
            params,
            config.ranking_weights,
            config.freshness,
            config.trust_level_weights,
            config.bucket_score,
        )
        trend_scored = diversify(trend_scored, config.diversification)
        filled.fallback_trend = trend_scored[:deficit]
        if filled.fallback_trend:
            filled.fallback_reasons[SlotType.FALLBACK_TREND] = "overall_short"

    # 7. reasons — LLM 1회 batch (필드 갱신 위해 후속 persist 와 함께).
    all_cards: list[ScoredCandidate] = (
        filled.core + filled.adjacent + filled.discovery + filled.fallback_trend
    )
    reasons = await generate_reasons(provider, all_cards, user_id=user.user_id)

    # 8. persist — db.commit 은 caller 책임.
    doc_to_rec_id = await _persist_recommendations(
        db, user.user_id, filled, reasons
    )
    # 9. materialize cards (chip fetch + score 마스킹).
    chips = await _fetch_topic_chips(db, list(doc_to_rec_id.keys()))
    cards = _filled_slots_to_cards(
        filled, doc_to_rec_id, reasons, chips=chips
    )
    response = DashboardResponse(
        user_id=user.user_id,
        cards=cards,
        slots=_serialize_slot_summaries(filled),
        generated_at=datetime.now(UTC),
        cache="miss",
        cold_start=False,
    )
    return DashboardBuildResult(response=response)


__all__ = [
    "ColdStartInProgress",
    "DashboardBuildResult",
    "build_dashboard",
]
