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
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.dedup import normalize_title
from app.config import Settings
from app.contracts import (
    ContentType,
    SentinelSource,
    SlotType,
    TopicChip,
)
from app.db.models import (
    ClickbaitResult,
    CSOTopic,
    Document,
    DocumentTopic,
    DynamicLeafTopic,
    HiddenDocument,
    NotInterestedTopic,
    Recommendation,
    RecommendationSlot,
    SavedDocument,
    Source,
    User,
    UserInterestState,
)
from app.interest.config_loader import InterestParams
from app.llm_provider.protocol import LLMProvider
from app.profile.config_loader import load_profile_config
from app.profile.service import get_user_profile
from app.traversal import queries as trav_queries

from .candidates import (
    CandidateRow,
    query_adjacent,
    query_core,
    query_discovery_fusion,
    query_discovery_reincarnation,
    query_discovery_trend,
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


async def _has_only_cold_start_recommendations(
    db: AsyncSession, user_id: UUID
) -> bool:
    content_counts_stmt = (
        select(Document.content_type, func.count(Recommendation.recommendation_id))
        .join(Document, Document.document_id == Recommendation.document_id)
        .where(Recommendation.user_id == user_id)
        .group_by(Document.content_type)
    )
    content_counts = {
        str(row.content_type): int(row[1])
        for row in await db.execute(content_counts_stmt)
    }
    pseudo_count = content_counts.get("pseudo_cold_start", 0)
    non_pseudo_count = sum(
        count
        for content_type, count in content_counts.items()
        if content_type != "pseudo_cold_start"
    )
    return pseudo_count > 0 and non_pseudo_count == 0


async def _is_cold_start(db: AsyncSession, user: User) -> bool:
    """active trace 0개 AND UserInterestState 행동 신호 0 (alpha_prior 만)."""
    active_count = await trav_queries.count_active_traces(db, user.user_id)
    if active_count > 0:
        return await _has_only_cold_start_recommendations(db, user.user_id)
    # boost_applied_at_active_day 가 있는 row 는 onboarding boost — 행동 신호 X.
    # 행동 신호 = boost 없이 long_alpha 가 prior 보다 큰 row.
    stmt = select(func.count(UserInterestState.state_id)).where(
        UserInterestState.user_id == user.user_id,
        UserInterestState.boost_applied_at_active_day.is_(None),
        # alpha 가 prior 보다 1.0 이상 큰 row 만 — view event 등 작은 신호는 제외.
        UserInterestState.long_alpha > 2.0,
    )
    behavioral_count = (await db.execute(stmt)).scalar_one()
    if behavioral_count == 0:
        return True

    # A9 demo guard: save/hide/not-interested feedback creates behavioral
    # interest rows before A7/A8 can generate normal, non-pseudo candidates.
    # Keep serving the cold-start recommendation set until real recommendation
    # rows exist, otherwise one feedback action flips the dashboard to an empty
    # normal-ranking result because pseudo_cold_start documents are excluded
    # from the regular candidate path.
    return await _has_only_cold_start_recommendations(db, user.user_id)


async def _load_cold_start_dashboard(
    db: AsyncSession,
    user: User,
    params: InterestParams,
    config: RecommendationConfig,
) -> DashboardResponse:
    """이미 cold-start 완료된 사용자 — 저장된 Recommendation rows 를 serialize."""
    today_recs = await _select_today_recommendations(db, user.user_id)
    if not today_recs:
        today_recs = await _select_latest_recommendations(db, user.user_id)
    hidden_docs = await _fetch_hidden_documents(
        db, user.user_id, [r.document_id for r in today_recs]
    )
    visible_recs = [r for r in today_recs if r.document_id not in hidden_docs]
    backfill_cards: list[RecommendationCard] = []
    backfill_recs: list[Recommendation] = []
    if len(visible_recs) < config.slot_targets.total:
        backfill_recs, backfill_cards = await _backfill_cold_start_dashboard(
            db,
            user,
            visible_recs=visible_recs,
            hidden_docs=hidden_docs,
            params=params,
            config=config,
        )
    cards = await _materialize_cards(db, visible_recs, user.user_id)
    cards.extend(backfill_cards)
    slot_summaries = _serialize_slot_summaries_from_recs(
        visible_recs + backfill_recs
    )
    cards, slot_summaries = await _ensure_dashboard_card_count(
        db,
        user,
        cards=cards,
        slots=slot_summaries,
        params=params,
        config=config,
    )
    return DashboardResponse(
        user_id=user.user_id,
        cards=cards,
        slots=slot_summaries,
        generated_at=datetime.now(UTC),
        cache="miss",
        cold_start=True,
    )


async def _backfill_cold_start_dashboard(
    db: AsyncSession,
    user: User,
    *,
    visible_recs: list[Recommendation],
    hidden_docs: set[UUID],
    params: InterestParams,
    config: RecommendationConfig,
) -> tuple[list[Recommendation], list[RecommendationCard]]:
    """cold-start 저장 추천이 숨김 처리로 10개 미만이면 일반 문서로 보충.

    cold-start 추천은 기존 Recommendation row 를 복원하는 경로라 사용자가 1~2개 숨기면
    응답에서만 빠지고 부족분이 생길 수 있다. UI-02 의 "항상 10 카드" 계약을 지키기
    위해 이미 보이는 문서와 숨김 문서를 제외하고 문서 풀에서 fallback_trend 를 채운다.
    """
    deficit = config.slot_targets.total - len(visible_recs)
    if deficit <= 0:
        return [], []

    excluded = {r.document_id for r in visible_recs} | hidden_docs
    state_index = await _fetch_state_index(db, user.user_id)
    rows = await build_trend_fallback(
        db, user.user_id, excluded, deficit, cfg=config.fallback
    )
    if len(rows) < deficit:
        rows.extend(
            await _query_any_backfill_documents(
                db,
                user.user_id,
                exclude_ids=excluded | {r.document_id for r in rows},
                limit=deficit - len(rows),
            )
        )
    if len(rows) < deficit:
        rows.extend(
            await _create_demo_backfill_candidates(
                db,
                user.user_id,
                exclude_ids=excluded | {r.document_id for r in rows},
                limit=deficit - len(rows),
            )
        )
    if not rows:
        return [], []

    scored = score_candidates(
        rows,
        state_index,
        params,
        config.ranking_weights,
        config.freshness,
        config.trust_level_weights,
        config.bucket_score,
    )
    scored = diversify(scored, config.diversification)[:deficit]
    reasons = {
        c.document_id: "숨긴 문서를 제외하고 최근 신뢰도 높은 자료로 보충했습니다."
        for c in scored
    }
    doc_to_rec_id = await _persist_backfill_recommendations(
        db, user.user_id, scored, reasons
    )
    chips = await _fetch_topic_chips(db, list(doc_to_rec_id.keys()))
    filled = FilledSlots(fallback_trend=scored)
    cards = _filled_slots_to_cards(
        filled, doc_to_rec_id, reasons, chips=chips
    )
    recs = [
        Recommendation(
            recommendation_id=rec_id,
            user_id=user.user_id,
            document_id=doc_id,
            slot_type=SlotType.FALLBACK_TREND.value,
            reason=reasons.get(doc_id),
            score=next((c.score for c in scored if c.document_id == doc_id), None),
        )
        for doc_id, rec_id in doc_to_rec_id.items()
    ]
    return recs, cards


async def _query_any_backfill_documents(
    db: AsyncSession,
    user_id: UUID,
    *,
    exclude_ids: set[UUID],
    limit: int,
) -> list[CandidateRow]:
    """최근 trend fallback 이 부족할 때 쓰는 넓은 보충 후보.

    시연 데이터는 published_at 이 오래됐거나 trust_level 이 high 가 아닐 수 있어,
    숨김/저장/관심없음/clickbait 제외 조건은 유지하면서 topic 매핑 없는 문서까지
    전체 문서 풀로 넓힌다.
    """
    if limit <= 0:
        return []
    stmt = (
        select(
            Document.document_id,
            Document.title,
            Document.source_id,
            Source.name.label("source_name"),
            Source.source_type.label("source_type"),
            Source.trust_level.label("trust_level"),
            Document.published_at,
            Document.created_at,
            DocumentTopic.cso_topic_id,
            DocumentTopic.leaf_topic_id,
            DocumentTopic.confidence.label("topic_confidence"),
            DynamicLeafTopic.status.label("leaf_status"),
            DynamicLeafTopic.label.label("leaf_label"),
            CSOTopic.label.label("cso_label"),
        )
        .join(Source, Source.source_id == Document.source_id)
        .outerjoin(DocumentTopic, DocumentTopic.document_id == Document.document_id)
        .outerjoin(
            DynamicLeafTopic,
            DynamicLeafTopic.leaf_topic_id == DocumentTopic.leaf_topic_id,
        )
        .outerjoin(CSOTopic, CSOTopic.cso_topic_id == DocumentTopic.cso_topic_id)
        .where(
            ~exists().where(
                SavedDocument.user_id == user_id,
                SavedDocument.document_id == Document.document_id,
            ),
            ~exists().where(
                HiddenDocument.user_id == user_id,
                HiddenDocument.document_id == Document.document_id,
            ),
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
            ),
            ~exists().where(
                ClickbaitResult.document_id == Document.document_id,
                ClickbaitResult.decision == "clickbait",
            ),
        )
        .order_by(
            Document.published_at.desc().nulls_last(),
            Document.created_at.desc(),
        )
        .limit(limit * 4)
    )
    rows_raw = (await db.execute(stmt)).all()
    seen: set[UUID] = set()
    result: list[CandidateRow] = []
    for r in rows_raw:
        if r.document_id in exclude_ids or r.document_id in seen:
            continue
        seen.add(r.document_id)
        result.append(
            CandidateRow(
                document_id=r.document_id,
                title=r.title,
                source_id=r.source_id,
                source_name=r.source_name,
                source_type=r.source_type,
                trust_level=r.trust_level,
                published_at=r.published_at or r.created_at,
                cso_topic_id=r.cso_topic_id,
                leaf_topic_id=r.leaf_topic_id,
                leaf_status=r.leaf_status,
                leaf_label=r.leaf_label,
                cso_label=r.cso_label,
                topic_confidence=float(r.topic_confidence or 0.2),
            )
        )
        if len(result) >= limit:
            break
    return result


async def _create_demo_backfill_candidates(
    db: AsyncSession,
    user_id: UUID,
    *,
    exclude_ids: set[UUID],
    limit: int,
) -> list[CandidateRow]:
    """데모 DB에 문서 풀이 10개보다 작을 때 부족분을 즉시 보충.

    실제 운영에서는 collection job 이 문서 풀을 넓히지만, 시연 DB는 cold-start 10개만
    있는 경우가 많다. 숨김 후에도 UI-02 의 10 카드 계약을 보여주기 위해 내부 pseudo
    문서를 생성한다.
    """
    if limit <= 0:
        return []
    source = (
        await db.execute(
            select(Source).where(Source.name == SentinelSource.COLD_START_PSEUDO_NAME)
        )
    ).scalar_one_or_none()
    if source is None:
        return []

    topic_rows = (
        await db.execute(
            select(CSOTopic.cso_topic_id, CSOTopic.label)
            .join(UserInterestState, UserInterestState.cso_topic_id == CSOTopic.cso_topic_id)
            .where(
                UserInterestState.user_id == user_id,
                UserInterestState.cso_topic_id.is_not(None),
            )
            .order_by(UserInterestState.long_score.desc(), CSOTopic.label.asc())
            .limit(max(limit, 1))
        )
    ).all()
    if len(topic_rows) < max(limit, 1):
        seen_topic_ids = {row.cso_topic_id for row in topic_rows}
        fallback_topics = (
            await db.execute(
                select(CSOTopic.cso_topic_id, CSOTopic.label)
                .where(CSOTopic.cso_topic_id.not_in(seen_topic_ids))
                .order_by(CSOTopic.label.asc())
                .limit(max(limit, 1) - len(topic_rows))
            )
        ).all()
        topic_rows.extend(fallback_topics)
    if not topic_rows:
        return []

    now = datetime.now(UTC)
    result: list[CandidateRow] = []
    max_attempts = max(limit * 3, limit + len(topic_rows))
    for idx in range(max_attempts):
        if len(result) >= limit:
            break
        topic = topic_rows[idx % len(topic_rows)]
        doc_id = uuid4()
        if doc_id in exclude_ids:
            continue
        title = (
            f"{topic.label} follow-up briefing "
            f"{now:%Y%m%d}-{len(exclude_ids) + idx + 1}"
        )
        url = f"internal://recommendation-backfill/{user_id}/{doc_id}"
        await db.execute(
            pg_insert(Document)
            .values(
                document_id=doc_id,
                source_id=source.source_id,
                title=title,
                normalized_title=normalize_title(title),
                url=url,
                canonical_url=url,
                doi=None,
                summary=f"{topic.label} 관련 후속 추천을 채우기 위한 데모 보충 자료입니다.",
                published_at=now,
                content_type=ContentType.PSEUDO_COLD_START.value,
                language="en",
                raw={
                    "publisher_label": "SKKU InSight",
                    "publisher_domain": "internal",
                    "demo_backfill": True,
                },
            )
            .on_conflict_do_nothing()
        )
        await db.execute(
            pg_insert(DocumentTopic)
            .values(
                id=uuid4(),
                document_id=doc_id,
                cso_topic_id=topic.cso_topic_id,
                leaf_topic_id=None,
                confidence=0.2,
            )
            .on_conflict_do_nothing()
        )
        result.append(
            CandidateRow(
                document_id=doc_id,
                title=title,
                source_id=source.source_id,
                source_name=source.name,
                source_type=source.source_type,
                trust_level=source.trust_level,
                published_at=now,
                cso_topic_id=topic.cso_topic_id,
                leaf_topic_id=None,
                leaf_status=None,
                leaf_label=None,
                cso_label=str(topic.label),
                topic_confidence=0.2,
            )
        )
    return result


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


def _dedupe_dashboard_cards(
    cards: list[RecommendationCard],
) -> list[RecommendationCard]:
    seen_docs: set[UUID] = set()
    seen_titles: set[str] = set()
    out: list[RecommendationCard] = []
    for card in cards:
        title_key = normalize_title(card.title)
        if card.document_id in seen_docs or title_key in seen_titles:
            continue
        seen_docs.add(card.document_id)
        seen_titles.add(title_key)
        out.append(card)
    return out


async def _fetch_all_hidden_documents(db: AsyncSession, user_id: UUID) -> set[UUID]:
    stmt = select(HiddenDocument.document_id).where(
        HiddenDocument.user_id == user_id
    )
    rows = (await db.execute(stmt)).scalars().all()
    return set(rows)


def _with_fallback_slot_count(
    slots: list[SlotSummary], added_count: int
) -> list[SlotSummary]:
    if added_count <= 0:
        return slots
    out: list[SlotSummary] = []
    updated = False
    for summary in slots:
        if summary.slot_type == SlotType.FALLBACK_TREND:
            out.append(
                summary.model_copy(
                    update={
                        "actual_count": summary.actual_count + added_count,
                        "fallback_reason": summary.fallback_reason
                        or "overall_short",
                    }
                )
            )
            updated = True
            continue
        out.append(summary)
    if not updated:
        out.append(
            SlotSummary(
                slot_type=SlotType.FALLBACK_TREND,
                target_count=0,
                actual_count=added_count,
                fallback_reason="overall_short",
            )
        )
    return out


async def _ensure_dashboard_card_count(
    db: AsyncSession,
    user: User,
    *,
    cards: list[RecommendationCard],
    slots: list[SlotSummary],
    params: InterestParams,
    config: RecommendationConfig,
) -> tuple[list[RecommendationCard], list[SlotSummary]]:
    target = config.slot_targets.total
    cards = _dedupe_dashboard_cards(cards)
    if len(cards) >= target:
        return cards[:target], slots

    hidden_docs = await _fetch_all_hidden_documents(db, user.user_id)
    excluded = {card.document_id for card in cards} | hidden_docs
    rows = await _create_demo_backfill_candidates(
        db,
        user.user_id,
        exclude_ids=excluded,
        limit=target - len(cards),
    )
    if not rows:
        return cards, slots

    state_index = await _fetch_state_index(db, user.user_id)
    scored = score_candidates(
        rows,
        state_index,
        params,
        config.ranking_weights,
        config.freshness,
        config.trust_level_weights,
        config.bucket_score,
    )
    scored = diversify(scored, config.diversification)[: target - len(cards)]
    reasons = {
        c.document_id: "추천 목록을 10개로 유지하기 위해 보충한 자료입니다."
        for c in scored
    }
    doc_to_rec_id = await _persist_backfill_recommendations(
        db, user.user_id, scored, reasons
    )
    chips = await _fetch_topic_chips(db, list(doc_to_rec_id.keys()))
    extra_cards = _filled_slots_to_cards(
        FilledSlots(fallback_trend=scored),
        doc_to_rec_id,
        reasons,
        chips=chips,
    )
    if not extra_cards:
        return cards, slots
    return (
        (cards + extra_cards)[:target],
        _with_fallback_slot_count(slots, len(extra_cards)),
    )


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


async def _persist_backfill_recommendations(
    db: AsyncSession,
    user_id: UUID,
    cards: list[ScoredCandidate],
    reasons: dict[UUID, str],
) -> dict[UUID, UUID]:
    """cold-start 부족분 보충용 fallback_trend Recommendation INSERT."""
    doc_to_rec_id: dict[UUID, UUID] = {}
    for c in cards:
        new_id = uuid4()
        reason = reasons.get(c.document_id, "")
        stmt = (
            pg_insert(Recommendation)
            .values(
                recommendation_id=new_id,
                user_id=user_id,
                document_id=c.document_id,
                slot_type=SlotType.FALLBACK_TREND.value,
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
            continue
        lookup = (
            await db.execute(
                select(Recommendation.recommendation_id)
                .where(
                    Recommendation.user_id == user_id,
                    Recommendation.document_id == c.document_id,
                    Recommendation.slot_type == SlotType.FALLBACK_TREND.value,
                    func.date(func.timezone("UTC", Recommendation.created_at))
                    == func.date(func.timezone("UTC", func.now())),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if lookup is not None:
            doc_to_rec_id[c.document_id] = lookup
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


def _resolve_seed_id(
    seed: dict[str, Any],
    cso_graph: nx.DiGraph,
    *,
    excluded: set[UUID],
) -> UUID | None:
    """seed dict 안 `cso_topic_id` 를 UUID 로 파싱 + cso_graph 멤버십 + excluded 외 확인.

    Codex R1 Suggested #3 (2026-05-19): excluded (trace_path_csos) 안에 있는 노드는
    fusion / reincarnation bridge / seed 로 거부 — active path 의 core 슬롯 후보와
    중복 차단.
    """
    seed_id_raw = seed.get("cso_topic_id")
    if not seed_id_raw:
        return None
    try:
        seed_id = UUID(str(seed_id_raw))
    except (ValueError, TypeError):
        return None
    if seed_id in excluded:
        return None
    if seed_id not in cso_graph:
        return None
    return seed_id


async def _build_fusion_subslot(
    db: AsyncSession,
    profile: Any,
    cso_graph: nx.DiGraph,
    *,
    user_id: UUID,
    trace_path_csos: set[UUID],
) -> list[CandidateRow]:
    """slot 1 (Fusion) — fusion_candidates → broadening_seeds → trend fallback.

    Codex R1 Critical #2 + Suggested #3 + #4 fix (2026-05-19):
    - fusion_candidates 의 bridge_cso 가 trace_path_csos 안이면 거부 (Suggested #3)
    - 후보 풀이 doc 0개면 다음 fallback 진행 (Suggested #4)
    - 별도 sub-slot 반환 → engine 이 slot 별 1개씩 강제 (Critical #2)
    """
    if profile is not None:
        for candidate in profile.fusion_candidates or []:
            bridge_id = _resolve_seed_id(
                {"cso_topic_id": candidate.get("bridge_cso_topic_id")},
                cso_graph,
                excluded=trace_path_csos,
            )
            if bridge_id is None:
                continue
            rows = await query_discovery_fusion(db, user_id, bridge_id)
            if rows:
                return rows
        for seed in profile.broadening_seeds or []:
            seed_id = _resolve_seed_id(
                seed, cso_graph, excluded=trace_path_csos
            )
            if seed_id is None:
                continue
            rows = await query_discovery_fusion(db, user_id, seed_id)
            if rows:
                return rows
    return await query_discovery_trend(db, user_id, list(trace_path_csos))


async def _build_reincarnation_subslot(
    db: AsyncSession,
    profile: Any,
    cso_graph: nx.DiGraph,
    settings: Settings,
    *,
    user: User,
    trace_path_csos: set[UUID],
) -> list[CandidateRow]:
    """slot 2 (Reincarnation) — top_archived_trace → deepening_seeds → trend fallback.

    Codex R1 Critical #2 + Suggested #4 (2026-05-19): 별도 sub-slot 반환 + doc 결과
    기반 fallback 판단.
    """
    archived_trace = await trav_queries.get_top_archived_trace(
        db,
        user.user_id,
        score_tail_min=settings.USER_PROFILE_ARCHIVE_SCORE_TAIL_MIN,
        gap_days_min=settings.USER_PROFILE_REINCARNATION_GAP_DAYS_MIN,
        current_active_day=int(user.active_day_counter),
    )
    if archived_trace is not None and archived_trace.path:
        archived_leaves = await trav_queries.get_descendant_archived_leaves(
            db, user.user_id, trace=archived_trace
        )
        archived_leaf_ids = [lf.leaf_topic_id for lf in archived_leaves]
        tail_cso = archived_trace.path[-1]
        rows = await query_discovery_reincarnation(
            db, user.user_id, tail_cso, archived_leaf_ids
        )
        if rows:
            return rows
    if profile is not None:
        for seed in profile.deepening_seeds or []:
            seed_id = _resolve_seed_id(
                seed, cso_graph, excluded=trace_path_csos
            )
            if seed_id is None:
                continue
            rows = await query_discovery_fusion(db, user.user_id, seed_id)
            if rows:
                return rows
    return await query_discovery_trend(db, user.user_id, list(trace_path_csos))


async def _build_discovery_pools(
    db: AsyncSession,
    redis: aioredis.Redis,
    cso_graph: nx.DiGraph,
    settings: Settings,
    *,
    user: User,
    trace_path_csos: set[UUID],
) -> tuple[list[CandidateRow], list[CandidateRow]]:
    """A8-v2 discovery slot 본문 — `(fusion_pool, reincarnation_pool)` 별도 반환.

    Codex R1 Critical #2 fix (2026-05-19): 직전 `_build_discovery_pool_raw` 가 두
    source 를 untagged pool 로 통합 → ranking 시점에 의도된 "Fusion 1 + Reincarnation
    1" 슬롯 분배가 지켜지지 않음 (fusion 만 2 또는 reincarnation 만 2 가능). 본 함수는
    sub-slot 별 별도 list 반환 → caller (build_dashboard) 가 각 ranking + diversify
    + `[:1]` 강제.

    각 sub-slot 의 fallback chain:
    - Fusion: fusion_candidates → broadening_seeds → trust=high trend
    - Reincarnation: top_archived_trace → deepening_seeds → trust=high trend

    decisions.md §15 (A8-v2 결정 #1) + algorithms/recommendation-ranking.md §Discovery.
    """
    profile_config = load_profile_config(settings)
    profile = await get_user_profile(
        db,
        redis,
        user.user_id,
        cache_ttl_seconds=profile_config.cache_ttl_seconds,
    )
    fusion_pool = await _build_fusion_subslot(
        db, profile, cso_graph, user_id=user.user_id, trace_path_csos=trace_path_csos
    )
    reincarnation_pool = await _build_reincarnation_subslot(
        db,
        profile,
        cso_graph,
        settings,
        user=user,
        trace_path_csos=trace_path_csos,
    )
    return fusion_pool, reincarnation_pool


async def _build_discovery_pool_raw(
    db: AsyncSession,
    redis: aioredis.Redis,
    cso_graph: nx.DiGraph,
    settings: Settings,
    *,
    user: User,
    trace_path_csos: set[UUID],
) -> list[CandidateRow]:
    """**Deprecated** (2026-05-19) — `_build_discovery_pools` 의 untagged 통합 wrapper.

    backward-compat 만 유지. 신규 caller 는 `_build_discovery_pools` 직접 사용 권장.
    """
    fusion_pool, reincarnation_pool = await _build_discovery_pools(
        db, redis, cso_graph, settings, user=user, trace_path_csos=trace_path_csos
    )
    return fusion_pool + reincarnation_pool


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
        return DashboardBuildResult(
            response=await _load_cold_start_dashboard(db, user, params, config)
        )

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
    # Codex R1 Critical #2 fix (2026-05-19): fusion + reincarnation 별도 sub-slot →
    # ranking + diversify 별도 → 각 [:1] concat → fill_slots 가 source 별 1개씩 강제.
    (
        fusion_pool_raw,
        reincarnation_pool_raw,
    ) = await _build_discovery_pools(
        db,
        redis,
        cso_graph,
        settings,
        user=user,
        trace_path_csos=trace_path_csos,
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
    # Codex R1 Critical #2 fix (2026-05-19): fusion / reincarnation 별도 ranking + diversify.
    fusion_pool = score_candidates(
        fusion_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness,
        config.trust_level_weights,
        config.bucket_score,
    )
    reincarnation_pool = score_candidates(
        reincarnation_pool_raw,
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
    fusion_pool = diversify(fusion_pool, config.diversification)
    reincarnation_pool = diversify(reincarnation_pool, config.diversification)
    emerging_pool = diversify(emerging_pool, config.diversification)

    # Codex R1 Critical #2 fix — source 별 1개씩 강제. fill_slots 가 본 list 의 첫
    # 2개를 discovery 슬롯에 채우므로 fusion 1 + reincarnation 1 보장.
    discovery_pool = fusion_pool[:1] + reincarnation_pool[:1]

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
        if len(trend_raw) < deficit:
            trend_raw.extend(
                await _query_any_backfill_documents(
                    db,
                    user.user_id,
                    exclude_ids=excluded | {r.document_id for r in trend_raw},
                    limit=deficit - len(trend_raw),
                )
            )
        if len(trend_raw) < deficit:
            trend_raw.extend(
                await _create_demo_backfill_candidates(
                    db,
                    user.user_id,
                    exclude_ids=excluded | {r.document_id for r in trend_raw},
                    limit=deficit - len(trend_raw),
                )
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
    slot_summaries = _serialize_slot_summaries(filled)
    cards, slot_summaries = await _ensure_dashboard_card_count(
        db,
        user,
        cards=cards,
        slots=slot_summaries,
        params=params,
        config=config,
    )
    response = DashboardResponse(
        user_id=user.user_id,
        cards=cards,
        slots=slot_summaries,
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
