"""Fallback 룰 — recommendation-ranking.md §Fallback FR-42·FR-43.

FR-42 (slot 부족): 특정 slot 의 임계 통과 후보가 target 미달 → 같은 임계 통과
다른 slot 잉여로 대체 + `fallback_reason="slot_X_short_by_N"` 기록.

FR-43 (전체 < 10): 모든 slot 합산 < 10 → 다단계 trend fallback:
1) 인접 hops=2 (current 의 2-hop 그래프 이웃) trust=high 트렌드
2) 신뢰 소스 전체 트렌드 (사용자 토픽 무관) 최근 7 wallclock days
3) 신뢰 소스 archive (최근 30 wallclock days)
각 단계 slot_type=`fallback_trend`, fallback_reason="overall_short".

§11.#2 방어: emerging quota race — emerging_pool 후보 부재 시 active 회수.
emerging vs active 구분은 candidates.py 시점에 단일 SQL 로 가져온 leaf_status
컬럼 활용 → 별도 SQL 호출 X.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import SlotType
from app.db.models import (
    ClickbaitResult,
    Document,
    DocumentTopic,
    HiddenDocument,
    NotInterestedTopic,
    SavedDocument,
    Source,
)

from .candidates import CandidateRow, _row_to_candidate
from .config_loader import (
    ConfidenceThresholds,
    CoreSlotQuota,
    FallbackConfig,
    SlotTargets,
)
from .ranking import ScoredCandidate


@dataclass(slots=True)
class FilledSlots:
    """slot 별 채워진 카드 + fallback reason. SlotType.{CORE, ADJACENT, DISCOVERY, FALLBACK_TREND}."""

    core: list[ScoredCandidate] = field(default_factory=list)
    adjacent: list[ScoredCandidate] = field(default_factory=list)
    discovery: list[ScoredCandidate] = field(default_factory=list)
    fallback_trend: list[ScoredCandidate] = field(default_factory=list)
    # slot 별 fallback_reason — None 이면 정상.
    fallback_reasons: dict[SlotType, str | None] = field(default_factory=dict)

    def total(self) -> int:
        return (
            len(self.core)
            + len(self.adjacent)
            + len(self.discovery)
            + len(self.fallback_trend)
        )

    def all_document_ids(self) -> set[UUID]:
        ids: set[UUID] = set()
        for bucket in (self.core, self.adjacent, self.discovery, self.fallback_trend):
            for c in bucket:
                ids.add(c.document_id)
        return ids


def _passes_threshold(
    candidate: ScoredCandidate,
    slot: SlotType,
    thresholds: ConfidenceThresholds,
) -> bool:
    """slot 별 confidence_thresholds 통과 여부.

    discovery 는 추가로 trust_level='high' 요구 (recommendation.toml).
    """
    if slot == SlotType.CORE:
        return candidate.topic_match >= thresholds.core_min_topic_match
    if slot == SlotType.ADJACENT:
        return candidate.topic_match >= thresholds.adjacent_min_topic_match
    if slot == SlotType.DISCOVERY:
        if candidate.trust_level != thresholds.discovery_required_trust_level:
            return False
        return candidate.topic_match >= thresholds.discovery_min_topic_match
    # FALLBACK_* — threshold X (이미 fallback 단계라 통과)
    return True


def fill_slots(
    *,
    core_pool: list[ScoredCandidate],
    adjacent_pool: list[ScoredCandidate],
    discovery_pool: list[ScoredCandidate],
    emerging_pool: list[ScoredCandidate],
    targets: SlotTargets,
    thresholds: ConfidenceThresholds,
    quota: CoreSlotQuota,
) -> FilledSlots:
    """slot 별 fill — emerging quota 우선 + threshold 통과 + FR-42 fallback.

    절차:
    1) core 슬롯: emerging_pool 에서 quota=1 우선 (없으면 active 회수). 잔여 (target-quota_filled)
       는 core_pool 에서 threshold 통과 score DESC 선택.
    2) adjacent / discovery: 각 pool 에서 threshold 통과 score DESC.
    3) 미달 slot → FR-42 — 다른 slot 잉여 (target 외, threshold 통과) 로 대체.
       대체 시 slot_type 은 원본 slot 유지 (즉 core 가 부족하면 다른 slot 잉여를 가져와도
       원본 ScoredCandidate 자체는 그대로) — fallback_reason 만 기록.
    """
    filled = FilledSlots()
    used_doc_ids: set[UUID] = set()

    # === 1. core: emerging quota → active 회수 ===
    emerging_taken = 0
    for c in emerging_pool:
        if emerging_taken >= quota.emerging_leaf_quota_in_core:
            break
        if c.document_id in used_doc_ids:
            continue
        if not _passes_threshold(c, SlotType.CORE, thresholds):
            continue
        filled.core.append(c)
        used_doc_ids.add(c.document_id)
        emerging_taken += 1

    core_remaining = targets.core - len(filled.core)
    for c in core_pool:
        if core_remaining <= 0:
            break
        if c.document_id in used_doc_ids:
            continue
        if not _passes_threshold(c, SlotType.CORE, thresholds):
            continue
        filled.core.append(c)
        used_doc_ids.add(c.document_id)
        core_remaining -= 1

    # === 2. adjacent / discovery ===
    for c in adjacent_pool:
        if len(filled.adjacent) >= targets.adjacent:
            break
        if c.document_id in used_doc_ids:
            continue
        if not _passes_threshold(c, SlotType.ADJACENT, thresholds):
            continue
        filled.adjacent.append(c)
        used_doc_ids.add(c.document_id)

    for c in discovery_pool:
        if len(filled.discovery) >= targets.discovery:
            break
        if c.document_id in used_doc_ids:
            continue
        if not _passes_threshold(c, SlotType.DISCOVERY, thresholds):
            continue
        filled.discovery.append(c)
        used_doc_ids.add(c.document_id)

    # === 3. FR-42 fallback — 미달 slot 을 다른 slot 잉여 (threshold 통과) 로 대체 ===
    deficits: dict[SlotType, int] = {
        SlotType.CORE: targets.core - len(filled.core),
        SlotType.ADJACENT: targets.adjacent - len(filled.adjacent),
        SlotType.DISCOVERY: targets.discovery - len(filled.discovery),
    }
    pools_by_slot: dict[SlotType, list[ScoredCandidate]] = {
        SlotType.CORE: core_pool,
        SlotType.ADJACENT: adjacent_pool,
        SlotType.DISCOVERY: discovery_pool,
    }
    filled_by_slot: dict[SlotType, list[ScoredCandidate]] = {
        SlotType.CORE: filled.core,
        SlotType.ADJACENT: filled.adjacent,
        SlotType.DISCOVERY: filled.discovery,
    }
    for slot, deficit in list(deficits.items()):
        if deficit <= 0:
            continue
        original_deficit = deficit
        # donors: 잉여가 많은 순서. 자기 자신 제외.
        donors = sorted(
            [s for s in (SlotType.CORE, SlotType.ADJACENT, SlotType.DISCOVERY)
             if s != slot],
            key=lambda s: -(len(pools_by_slot[s]) - len(filled_by_slot[s])),
        )
        for donor in donors:
            if deficit <= 0:
                break
            extras = [
                c for c in pools_by_slot[donor]
                if c.document_id not in used_doc_ids
                and _passes_threshold(c, slot, thresholds)   # 받는 slot 의 임계
            ]
            take = extras[:deficit]
            for t in take:
                filled_by_slot[slot].append(t)
                used_doc_ids.add(t.document_id)
            deficit -= len(take)
        if deficit < original_deficit:
            filled.fallback_reasons[slot] = (
                f"slot_{slot.value}_short_by_{original_deficit}"
            )
        if deficit > 0:
            # FR-42: 저신뢰 강제 X — 미달 그대로 두고 FR-43 가 처리.
            if slot not in filled.fallback_reasons:
                filled.fallback_reasons[slot] = (
                    f"slot_{slot.value}_short_by_{original_deficit}"
                )
    return filled


async def build_trend_fallback(
    db: AsyncSession,
    user_id: UUID,
    exclude_document_ids: set[UUID],
    n: int,
    *,
    cfg: FallbackConfig,
) -> list[CandidateRow]:
    """FR-43 다단계 trend fallback.

    1) 인접 hops=2 trend (graph 의존이라 caller 가 미리 cso 집합 전달하는 게 깔끔)
       — 본 함수는 (2) (3) 만 처리. caller (engine) 가 (1) 시도 후 본 함수 호출.
    2) 신뢰 소스 전체 트렌드 — trust_level='high' 최근 trend_window_days 일.
    3) 신뢰 소스 archive — 최근 archive_window_days 일.

    return: CandidateRow list (ranking 미수행 — caller 가 ranking + serialize).
    """
    if n <= 0:
        return []
    now = datetime.now(UTC)
    trend_cutoff = now - timedelta(days=cfg.trend_window_days)
    archive_cutoff = now - timedelta(days=cfg.archive_window_days)

    # Stage 2: trust=high 최근 7d (trend_window_days)
    rows = await _query_trust_high_recent(
        db, user_id, since=trend_cutoff, exclude_ids=exclude_document_ids, limit=n
    )
    if len(rows) >= n:
        return rows[:n]
    # Stage 3: archive (~30d)
    needed = n - len(rows)
    already = exclude_document_ids | {r.document_id for r in rows}
    more = await _query_trust_high_recent(
        db, user_id, since=archive_cutoff, exclude_ids=already, limit=needed
    )
    rows.extend(more[:needed])
    return rows


async def _query_trust_high_recent(
    db: AsyncSession,
    user_id: UUID,
    *,
    since: datetime,
    exclude_ids: set[UUID],
    limit: int,
) -> list[CandidateRow]:
    """trust=high + published_at >= since + 6 AntiJoin + 제외 set."""
    stmt = (
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
            DocumentTopic.confidence.label("topic_confidence"),
        )
        .join(DocumentTopic, DocumentTopic.document_id == Document.document_id)
        .join(Source, Source.source_id == Document.source_id)
        .where(
            Source.trust_level == "high",
            Document.published_at.is_not(None),
            Document.published_at >= since,
            Document.content_type != "pseudo_cold_start",
            # AntiJoin 5종 (clickbait, saved, hidden, not_interested, exclude_ids)
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
        .order_by(Document.published_at.desc())
        .limit(limit * 4)   # dedup 후 N 부족 회피 위해 buffer
    )
    rows_raw = (await db.execute(stmt)).all()
    seen: set[UUID] = set()
    result: list[CandidateRow] = []
    for r in rows_raw:
        if r.document_id in exclude_ids or r.document_id in seen:
            continue
        seen.add(r.document_id)
        # CandidateRow 변환 — leaf_topic 정보 부재라 None
        result.append(
            CandidateRow(
                document_id=r.document_id,
                title=r.title,
                source_id=r.source_id,
                source_name=r.source_name,
                source_type=r.source_type,
                trust_level=r.trust_level,
                published_at=r.published_at,
                cso_topic_id=r.cso_topic_id,
                leaf_topic_id=r.leaf_topic_id,
                leaf_status=None,
                leaf_label=None,
                cso_label=None,
                topic_confidence=float(r.topic_confidence),
            )
        )
        if len(result) >= limit:
            break
    # _row_to_candidate 는 leaf_status·leaf_label 컬럼 부재라 사용 X — 직접 변환.
    _ = _row_to_candidate  # silence ruff unused-import (utility for tests)
    return result


__all__ = [
    "FilledSlots",
    "build_trend_fallback",
    "fill_slots",
]
