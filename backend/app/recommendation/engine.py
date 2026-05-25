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
from sqlalchemy import and_, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.dedup import normalize_title
from app.config import Settings
from app.contracts import (
    ContentType,
    EventType,
    RedisKey,
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
    UserEvent,
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

_SLOT_TARGET_COUNTS: dict[SlotType, int] = {
    SlotType.CORE: 5,
    SlotType.ADJACENT: 3,
    SlotType.DISCOVERY: 2,
}
_BASE_SLOT_ORDER = (SlotType.CORE, SlotType.ADJACENT, SlotType.DISCOVERY)
_FALLBACK_SLOT_ORDER = (SlotType.FALLBACK_ADJACENT, SlotType.FALLBACK_TREND)


class ColdStartInProgress(Exception):
    """cold-start 진행 중 — caller 가 HTTPException 202 변환."""


@dataclass(slots=True)
class DashboardBuildResult:
    """build_dashboard 결과 — response + persisted row info."""

    response: DashboardResponse


async def _cleanup_pseudo_recommendations(
    db: AsyncSession, redis: aioredis.Redis, user_id: UUID
) -> int:
    """(C-58, 2026-05-25) normal ranking 전환 시 옛 pseudo Recommendation row 정리.

    사용자 의도: "실제 수거하면 목업 다 없애". 옛 pseudo Document 자체는 보존 (legacy,
    backward-compat). Recommendation row 만 user-scoped DELETE.

    (C-58 followup, 2026-05-25) DELETE 가 실제로 row 정리 시 Redis cache 도 invalidate.
    그렇지 않으면 build_dashboard 가 cache hit 으로 옛 카드 (pseudo 포함) 반환.

    매 build_dashboard normal 분기 진입 시 호출 — idempotent (정리 후 0건 DELETE no-op).
    return: 삭제된 row 수 (통계용, 시연 검증 로그).
    """
    result = await db.execute(
        text(
            """
            DELETE FROM recommendation
            WHERE user_id = :uid
              AND document_id IN (
                  SELECT document_id FROM document
                  WHERE content_type = 'pseudo_cold_start'
              )
            """
        ),
        {"uid": user_id},
    )
    deleted = int(result.rowcount or 0)
    if deleted > 0:
        await redis.delete(RedisKey.recommendation_cache(user_id))
    return deleted


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
        # Once an active trace exists, normal ranking should take over. Keeping
        # the user on pseudo_cold_start rows here blocks newly collected
        # current/adjacent documents from surfacing after refresh.
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
    if behavioral_count == 0:
        return True

    # A9 demo guard: save/hide/not-interested feedback creates behavioral
    # interest rows before A7/A8 can generate normal, non-pseudo candidates.
    # Keep serving the cold-start recommendation set until real recommendation
    # rows exist, otherwise one feedback action flips the dashboard to an empty
    # normal-ranking result because pseudo_cold_start documents are excluded
    # from the regular candidate path.
    return await _has_only_cold_start_recommendations(db, user.user_id)


async def _is_collection_in_progress(
    redis: aioredis.Redis, user_id: UUID
) -> bool:
    """현재 user 의 collection_lock 보유 여부 — UI lock + refresh 차단 판단."""
    return bool(await redis.exists(RedisKey.collection_lock(user_id)))


async def _load_cold_start_dashboard(
    db: AsyncSession,
    redis: aioredis.Redis,
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
    cards = await _with_feedback_flags(db, user.user_id, cards)
    collection_in_progress = await _is_collection_in_progress(redis, user.user_id)
    return DashboardResponse(
        user_id=user.user_id,
        cards=cards,
        slots=slot_summaries,
        generated_at=datetime.now(UTC),
        cache="miss",
        cold_start=True,
        collection_in_progress=collection_in_progress,
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
            # (C-58 followup, 2026-05-25) pseudo 제외 — candidates.py:99 + fallback.py:285
            # 와 같은 정책. 본 함수에 filter 누락으로 옛 pseudo Document 가 fallback 으로
            # 끌어들여져 dashboard 상단 노출되던 결함 fix.
            Document.content_type != ContentType.PSEUDO_COLD_START.value,
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
    """(C-58, 2026-05-25) 폐기 — 항상 빈 list 반환.

    사용자 의도: dashboard 10개 미만이면 정직하게 빈 슬롯 표시 (실 자료 부족 신호).
    옛 동작은 sentinel `cold_start_pseudo` source + `content_type='pseudo_cold_start'`
    + "follow-up briefing" 가짜 자료 INSERT — 목업이 사용자에게 노출되는 본질이라 폐기.

    caller (engine.py 의 _backfill_cold_start_dashboard / build_dashboard fallback /
    _ensure_dashboard_card_count) 변경 0 — 빈 list 가 그대로 자연 흐름.

    옛 pseudo Document/Recommendation 은 보존 (legacy, backward-compat). normal ranking
    candidates query 가 `content_type != 'pseudo_cold_start'` filter 로 자동 제외.
    """
    _ = db, user_id, exclude_ids, limit  # 시그니처 유지 (caller 변경 회피)
    return []


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
    """UTC today rows 가 없으면 가장 최근 생성일의 Recommendation rows 를 복원.

    (C-51, 2026-05-24) **discovery slot 제외** — discovery 본질이 "매일 새 발견"
    (Fusion + Reincarnation 매일 새 select) 이라 어제 discovery 카드 표시 시 의미 깨짐.
    core/adjacent/fallback_* 은 fallback 그대로 (사용자 안정성).
    """
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
            Recommendation.slot_type != SlotType.DISCOVERY.value,
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
    not_interested_set = await _fetch_not_interested_documents(db, user_id, doc_ids)

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
                saved=rec.document_id in saved_set,
                hidden=rec.document_id in hidden_set,
                not_interested=rec.document_id in not_interested_set,
            )
        )
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


async def _fetch_not_interested_documents(
    db: AsyncSession, user_id: UUID, document_ids: list[UUID]
) -> set[UUID]:
    """document_ids 중 문서 단위 관심 없음으로 숨겨진 문서."""
    if not document_ids:
        return set()
    stmt = select(HiddenDocument.document_id).where(
        HiddenDocument.user_id == user_id,
        HiddenDocument.document_id.in_(document_ids),
        exists().where(
            UserEvent.user_id == user_id,
            UserEvent.document_id == HiddenDocument.document_id,
            UserEvent.event_type == EventType.NOT_INTERESTED.value,
        ),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return set(rows)


async def _with_feedback_flags(
    db: AsyncSession,
    user_id: UUID,
    cards: list[RecommendationCard],
) -> list[RecommendationCard]:
    """Dashboard cards 에 저장/숨김/관심 없음 상태를 최신 DB 기준으로 주입."""
    doc_ids = [card.document_id for card in cards]
    saved_set = await _fetch_saved_documents(db, user_id, doc_ids)
    hidden_set = await _fetch_hidden_documents(db, user_id, doc_ids)
    not_interested_set = await _fetch_not_interested_documents(db, user_id, doc_ids)
    return [
        card.model_copy(
            update={
                "saved": card.document_id in saved_set,
                "hidden": card.document_id in hidden_set,
                "not_interested": card.document_id in not_interested_set,
            }
        )
        for card in cards
    ]


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


def _reconcile_slot_summaries_with_cards(
    cards: list[RecommendationCard], slots: list[SlotSummary]
) -> list[SlotSummary]:
    """최종 노출 카드 기준으로 슬롯 카운트를 다시 계산한다.

    Recommendation rows 는 refresh/backfill 과정에서 같은 UTC day 안에 누적될 수 있다.
    반면 DashboardResponse.cards 는 dedupe + hidden 제외 + 10개 cap 을 거친 최종 화면
    목록이므로, SlotSummary 는 최종 cards 에서 재계산해야 UI 숫자 합이 10과 맞는다.
    """
    existing_by_slot = {summary.slot_type: summary for summary in slots}
    counts: dict[SlotType, int] = {}
    for card in cards:
        counts[card.slot_type] = counts.get(card.slot_type, 0) + 1

    reconciled: list[SlotSummary] = []
    for slot in _BASE_SLOT_ORDER:
        existing = existing_by_slot.get(slot)
        reconciled.append(
            SlotSummary(
                slot_type=slot,
                target_count=existing.target_count if existing else _SLOT_TARGET_COUNTS[slot],
                actual_count=counts.get(slot, 0),
                fallback_reason=existing.fallback_reason if existing else None,
            )
        )

    for slot in _FALLBACK_SLOT_ORDER:
        count = counts.get(slot, 0)
        if count <= 0:
            continue
        existing = existing_by_slot.get(slot)
        reconciled.append(
            SlotSummary(
                slot_type=slot,
                target_count=existing.target_count if existing else 0,
                actual_count=count,
                fallback_reason=(
                    existing.fallback_reason
                    if existing and existing.fallback_reason
                    else "overall_short"
                ),
            )
        )
    return reconciled


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
        final_cards = cards[:target]
        return final_cards, _reconcile_slot_summaries_with_cards(final_cards, slots)

    hidden_docs = await _fetch_all_hidden_documents(db, user.user_id)
    excluded = {card.document_id for card in cards} | hidden_docs
    rows = await _create_demo_backfill_candidates(
        db,
        user.user_id,
        exclude_ids=excluded,
        limit=target - len(cards),
    )
    if not rows:
        return cards, _reconcile_slot_summaries_with_cards(cards, slots)

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
        return cards, _reconcile_slot_summaries_with_cards(cards, slots)
    final_cards = (cards + extra_cards)[:target]
    return final_cards, _reconcile_slot_summaries_with_cards(
        final_cards, _with_fallback_slot_count(slots, len(extra_cards))
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
    *,
    origin_metadata: dict[UUID, tuple[str, UUID]] | None = None,
) -> dict[UUID, UUID]:
    """Recommendation + RecommendationSlot rows INSERT.

    §11.#2 방어: daily UNIQUE race — `pg_insert(...).on_conflict_do_nothing()` 패턴.
    같은 (user, doc, slot, date) 가 이미 있으면 skip (refresh fallback 경로 안전).

    (C-53, 2026-05-24) `origin_metadata: dict[document_id, (origin_type, origin_ref)]`
    인자 — discovery sub-slot 카드의 promotion 추적. Reincarnation 카드 = (`reincarnation`,
    archived_trace_id). Fusion 카드 = (`fusion`, bridge_cso_topic_id). 미매핑 = NULL.

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
    origin_metadata = origin_metadata or {}
    for slot, cards in bucket_iter:
        for c in cards:
            new_id = uuid4()
            reason = reasons.get(c.document_id, "")
            origin = origin_metadata.get(c.document_id)
            origin_type = origin[0] if origin else None
            origin_ref = origin[1] if origin else None
            stmt = (
                pg_insert(Recommendation)
                .values(
                    recommendation_id=new_id,
                    user_id=user_id,
                    document_id=c.document_id,
                    slot_type=slot.value,
                    reason=reason[:255] if reason else None,
                    score=c.score,
                    origin_type=origin_type,
                    origin_ref=origin_ref,
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
) -> tuple[list[CandidateRow], UUID | None]:
    """slot 1 (Fusion) — fusion_candidates → broadening_seeds → trend fallback.

    Codex R1 Critical #2 + Suggested #3 + #4 fix (2026-05-19):
    - fusion_candidates 의 bridge_cso 가 trace_path_csos 안이면 거부 (Suggested #3)
    - 후보 풀이 doc 0개면 다음 fallback 진행 (Suggested #4)
    - 별도 sub-slot 반환 → engine 이 slot 별 1개씩 강제 (Critical #2)

    (C-53, 2026-05-24) 반환 tuple — `(rows, bridge_cso_id)` 형태.
    bridge_cso_id = 본 sub-slot 의 origin metadata (promotion 추적). fallback
    경로 (broadening seed / trend) 진입 시 None — promotion 비대상.
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
                return rows, bridge_id
        for seed in profile.broadening_seeds or []:
            seed_id = _resolve_seed_id(
                seed, cso_graph, excluded=trace_path_csos
            )
            if seed_id is None:
                continue
            rows = await query_discovery_fusion(db, user_id, seed_id)
            if rows:
                return rows, None  # broadening seed = promotion 비대상
    return (
        await query_discovery_trend(db, user_id, list(trace_path_csos)),
        None,
    )


async def _build_reincarnation_subslot(
    db: AsyncSession,
    profile: Any,
    cso_graph: nx.DiGraph,
    settings: Settings,
    *,
    user: User,
    trace_path_csos: set[UUID],
) -> tuple[list[CandidateRow], UUID | None]:
    """slot 2 (Reincarnation) — softmax sampled archived trace → deepening_seeds → trend.

    Codex R1 Critical #2 + Suggested #4 (2026-05-19): 별도 sub-slot 반환 + doc 결과
    기반 fallback 판단.

    (C-53, 2026-05-24) get_top_archived_trace (deterministic top-1) → softmax sampling
    교체. 매일 다양한 archived trace 부활 — "매일 새 발견" 본질 정합.
    반환 tuple — `(rows, archived_trace_id)` 형태. 후자 = promotion metadata (Reincarnation
    카드 save 시 status archived→active 대상). fallback 경로 진입 시 None.
    """
    from app.profile.sampling import softmax_sample_trace

    archived_pool = await trav_queries.get_archived_traces_with_score(
        db,
        user.user_id,
        score_tail_min=settings.USER_PROFILE_ARCHIVE_SCORE_TAIL_MIN,
        limit=settings.USER_PROFILE_INPUT_ARCHIVE_MAX,
    )
    archived_trace = softmax_sample_trace(
        archived_pool,
        temperature=settings.REINCARNATION_SAMPLING_TEMPERATURE,
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
            return rows, archived_trace.trace_id
    if profile is not None:
        for seed in profile.deepening_seeds or []:
            seed_id = _resolve_seed_id(
                seed, cso_graph, excluded=trace_path_csos
            )
            if seed_id is None:
                continue
            rows = await query_discovery_fusion(db, user.user_id, seed_id)
            if rows:
                return rows, None  # deepening seed = promotion 비대상
    return (
        await query_discovery_trend(db, user.user_id, list(trace_path_csos)),
        None,
    )


async def _build_discovery_pools(
    db: AsyncSession,
    redis: aioredis.Redis,
    cso_graph: nx.DiGraph,
    settings: Settings,
    *,
    user: User,
    trace_path_csos: set[UUID],
) -> tuple[list[CandidateRow], list[CandidateRow], UUID | None, UUID | None]:
    """A8-v2 discovery slot 본문 — `(fusion_pool, reincarnation_pool, bridge_cso, archived_trace_id)` 반환.

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
    fusion_pool, fusion_bridge_id = await _build_fusion_subslot(
        db, profile, cso_graph, user_id=user.user_id, trace_path_csos=trace_path_csos
    )
    reincarnation_pool, reincarnation_trace_id = await _build_reincarnation_subslot(
        db,
        profile,
        cso_graph,
        settings,
        user=user,
        trace_path_csos=trace_path_csos,
    )
    return fusion_pool, reincarnation_pool, fusion_bridge_id, reincarnation_trace_id


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
    fusion_pool, reincarnation_pool, _, _ = await _build_discovery_pools(
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
            response=await _load_cold_start_dashboard(
                db, redis, user, params, config
            )
        )
    # (C-58, 2026-05-25) normal ranking 전환 시 옛 pseudo Recommendation 자동 정리.
    # (C-58 followup) DELETE 발생 시 Redis cache 도 invalidate (caller 가 매 호출 cache
    # hit 검사하므로 stale pseudo 카드가 cache 에서 복원되는 race 차단).
    # idempotent — 이미 정리된 user 는 0건 DELETE + cache invalidate skip.
    await _cleanup_pseudo_recommendations(db, redis, user.user_id)

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
    # (C-53, 2026-05-24) 추가 metadata 반환: bridge_cso (fusion) / archived_trace_id
    # (reincarnation) — _persist_recommendations 가 origin_type/origin_ref 저장.
    (
        fusion_pool_raw,
        reincarnation_pool_raw,
        fusion_bridge_cso_id,
        reincarnation_archived_trace_id,
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

    # 3. ranking (slot 별). (C-51 / C-53 followup) slot 별 freshness cfg 사용 —
    # core 30d / adjacent 14d / discovery = 1.0 강제 (코드 상수 _UNITY_FRESHNESS,
    # decay 자체 부재 = "매일 새 발견" 본질). recommendation.toml [freshness.core/adjacent]
    # sub-table + freshness_for_slot 내부 분기.
    core_pool = score_candidates(
        core_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness_for_slot(SlotType.CORE.value),
        config.trust_level_weights,
        config.bucket_score,
    )
    adjacent_pool = score_candidates(
        adjacent_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness_for_slot(SlotType.ADJACENT.value),
        config.trust_level_weights,
        config.bucket_score,
    )
    # Codex R1 Critical #2 fix (2026-05-19): fusion / reincarnation 별도 ranking + diversify.
    fusion_pool = score_candidates(
        fusion_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness_for_slot(SlotType.DISCOVERY.value),
        config.trust_level_weights,
        config.bucket_score,
    )
    reincarnation_pool = score_candidates(
        reincarnation_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness_for_slot(SlotType.DISCOVERY.value),
        config.trust_level_weights,
        config.bucket_score,
    )
    emerging_pool = score_candidates(
        emerging_pool_raw,
        state_index,
        params,
        config.ranking_weights,
        config.freshness_for_slot(SlotType.CORE.value),
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

    # (C-53, 2026-05-24) origin metadata 매핑 — discovery sub-slot 카드의 promotion 추적.
    # fusion_pool[0] 의 document_id → ('fusion', bridge_cso_id)
    # reincarnation_pool[0] 의 document_id → ('reincarnation', archived_trace_id)
    # caller (_persist_recommendations) 가 INSERT 시 origin_type/origin_ref 채움.
    origin_metadata: dict[UUID, tuple[str, UUID]] = {}
    if fusion_pool and fusion_bridge_cso_id is not None:
        origin_metadata[fusion_pool[0].document_id] = (
            "fusion", fusion_bridge_cso_id,
        )
    if reincarnation_pool and reincarnation_archived_trace_id is not None:
        origin_metadata[reincarnation_pool[0].document_id] = (
            "reincarnation", reincarnation_archived_trace_id,
        )

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
            config.freshness_for_slot(SlotType.FALLBACK_TREND.value),
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
        db, user.user_id, filled, reasons, origin_metadata=origin_metadata,
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
    cards = await _with_feedback_flags(db, user.user_id, cards)
    collection_in_progress = await _is_collection_in_progress(redis, user.user_id)
    response = DashboardResponse(
        user_id=user.user_id,
        cards=cards,
        slots=slot_summaries,
        generated_at=datetime.now(UTC),
        cache="miss",
        cold_start=False,
        collection_in_progress=collection_in_progress,
    )
    return DashboardBuildResult(response=response)


__all__ = [
    "ColdStartInProgress",
    "DashboardBuildResult",
    "build_dashboard",
]
