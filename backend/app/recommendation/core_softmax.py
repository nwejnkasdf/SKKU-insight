"""C-62 (2026-05-25) Core slot per-slot trace softmax sampling.

`build_dashboard` 의 core slot fill 본문 — 5 슬롯마다:
1. softmax(active traces, weights=score_tail) 으로 trace 1개 추첨.
2. 그 trace 의 path 산하 cso/leaf 영역에서 DocumentTopic.recommendation_score 최고 doc 선택.
3. 같은 trace 가 max_per_trace 번까지만 뽑힘 (soft cap, default 2).
4. day 내 deterministic — `hash(user_id, utc_date)` seed 로 random 안정.

옛 core slot fill (`fallback.fill_slots` 의 emerging quota + threshold + FR-42) 폐기 X —
adjacent/discovery 는 그대로 사용. 본 모듈은 core 만 교체.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import ContentType, SlotType, TraversalStatus
from app.db.models import (
    ClickbaitResult,
    Document,
    DocumentTopic,
    DynamicLeafTopic,
    HiddenDocument,
    NotInterestedTopic,
    SavedDocument,
    Source,
    UserCSOTraversal,
)

from .candidates import CandidateRow
from .config_loader import CoreSlotSoftmaxConfig
from .ranking import ScoredCandidate

logger = logging.getLogger(__name__)


def _day_seed(user_id: UUID, today: datetime | None = None) -> int:
    """day 내 deterministic random seed — `hash(user_id, utc_date)` (32-bit unsigned)."""
    today = today or datetime.now(UTC)
    payload = f"{user_id}:{today.strftime('%Y-%m-%d')}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:4], "big")


def _softmax_sample(
    items: Sequence[tuple[UUID, float]],
    rng: random.Random,
    *,
    temperature: float,
) -> UUID | None:
    """softmax 추첨 — 가중치 score_tail, 결과 trace_id 반환. 빈 list → None.

    Numerically stable softmax (max subtraction). temperature 0 또는 음수면 argmax 동치.
    """
    if not items:
        return None
    if temperature <= 0:
        # argmax fallback (deterministic top-1).
        return max(items, key=lambda x: x[1])[0]
    weights = [score / temperature for _, score in items]
    max_w = max(weights)
    exps = [math.exp(w - max_w) for w in weights]
    total = sum(exps)
    if total <= 0:
        # 모든 weight 동일 또는 underflow — uniform fallback.
        return rng.choice([t for t, _ in items])
    pick = rng.random() * total
    cumulative = 0.0
    for (trace_id, _), exp_w in zip(items, exps, strict=True):
        cumulative += exp_w
        if pick <= cumulative:
            return trace_id
    return items[-1][0]


async def _query_top_doc_for_trace(
    db: AsyncSession,
    user_id: UUID,
    trace_path: list[UUID],
    exclude_doc_ids: set[UUID],
) -> CandidateRow | None:
    """trace.path 산하 cso 영역의 DocumentTopic 중 recommendation_score 최고 doc 1개.

    AntiJoin (saved/hidden/not_interested/clickbait), pseudo 제외, 이미 used doc 제외.
    recommendation_score NULL 은 0 으로 취급 — LLM 미평가 row 는 후순위.
    """
    if not trace_path:
        return None
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
            DynamicLeafTopic.status.label("leaf_status"),
            DynamicLeafTopic.label.label("leaf_label"),
            DocumentTopic.confidence.label("topic_confidence"),
            DocumentTopic.recommendation_score.label("recommendation_score"),
        )
        .join(DocumentTopic, DocumentTopic.document_id == Document.document_id)
        .join(Source, Source.source_id == Document.source_id)
        .outerjoin(
            DynamicLeafTopic,
            DynamicLeafTopic.leaf_topic_id == DocumentTopic.leaf_topic_id,
        )
        .where(
            DocumentTopic.cso_topic_id.in_(trace_path),
            # pseudo_cold_start 제외 (C-58 의도 정합).
            Document.content_type != ContentType.PSEUDO_COLD_START.value,
            # leaf_status valid (active/emerging 또는 leaf=NULL).
            or_(
                DynamicLeafTopic.leaf_topic_id.is_(None),
                DynamicLeafTopic.status.in_(["emerging", "active"]),
            ),
            # AntiJoin 4 종 — saved/hidden/not_interested/clickbait. core 도 사용자가
            # 명시 hide / not_interested 한 doc 은 절대 차단.
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
        # recommendation_score DESC NULLS LAST, tie-break published_at DESC.
        .order_by(
            DocumentTopic.recommendation_score.desc().nulls_last(),
            Document.published_at.desc().nulls_last(),
        )
        .limit(20)  # buffer — exclude_doc_ids 처리 후 첫 통과 row 픽
    )
    rows = (await db.execute(stmt)).all()
    for r in rows:
        if r.document_id in exclude_doc_ids:
            continue
        return CandidateRow(
            document_id=r.document_id,
            title=r.title,
            source_id=r.source_id,
            source_name=r.source_name,
            source_type=r.source_type,
            trust_level=r.trust_level,
            published_at=r.published_at,
            cso_topic_id=r.cso_topic_id,
            leaf_topic_id=r.leaf_topic_id,
            leaf_status=r.leaf_status,
            leaf_label=r.leaf_label,
            cso_label=None,
            topic_confidence=float(r.topic_confidence or 0.0),
            recommendation_score=(
                int(r.recommendation_score)
                if r.recommendation_score is not None
                else None
            ),
        )
    return None


async def fill_core_slots_via_softmax(
    db: AsyncSession,
    user_id: UUID,
    target_core: int,
    cfg: CoreSlotSoftmaxConfig,
) -> list[CandidateRow]:
    """슬롯마다 trace softmax → 그 trace 영역 top doc. soft cap max_per_trace.

    Returns: target_core 개 미만일 수 있음 (trace 부족, 또는 trace 영역 doc 고갈).
    부족분은 caller (engine) 가 FR-43 trend fallback 으로 보충.
    """
    if target_core <= 0:
        return []
    traces_rows = (
        await db.execute(
            select(
                UserCSOTraversal.trace_id,
                UserCSOTraversal.path,
                UserCSOTraversal.score_tail,
            ).where(
                UserCSOTraversal.user_id == user_id,
                UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
            )
        )
    ).all()
    if not traces_rows:
        return []

    trace_path_by_id: dict[UUID, list[UUID]] = {
        r.trace_id: list(r.path) for r in traces_rows
    }
    trace_weights: list[tuple[UUID, float]] = [
        (r.trace_id, float(r.score_tail or 0.0)) for r in traces_rows
    ]

    rng = random.Random(_day_seed(user_id))
    pick_count: dict[UUID, int] = {t: 0 for t in trace_path_by_id}
    used_doc_ids: set[UUID] = set()
    filled: list[CandidateRow] = []

    for slot_idx in range(target_core):
        # soft cap — max_per_trace 도달한 trace 제외하고 재 sampling.
        available = [
            (tid, w)
            for (tid, w) in trace_weights
            if pick_count[tid] < cfg.max_per_trace
        ]
        if not available:
            # 모든 trace 가 cap 도달 — 더 채울 수 없음. FR-43 가 처리.
            logger.debug(
                "core softmax: all traces hit max_per_trace=%d at slot=%d user=%s",
                cfg.max_per_trace,
                slot_idx,
                user_id,
            )
            break
        picked = _softmax_sample(available, rng, temperature=cfg.temperature)
        if picked is None:
            break
        path = trace_path_by_id[picked]
        top_doc = await _query_top_doc_for_trace(db, user_id, path, used_doc_ids)
        if top_doc is None:
            # 본 trace 영역의 통과 가능 doc 고갈 — 본 trace 향후 sampling 에서도 cap 도달
            # 처리하면 무한 fallback. picked trace 의 cap 강제 도달로 마킹.
            pick_count[picked] = cfg.max_per_trace
            continue
        used_doc_ids.add(top_doc.document_id)
        pick_count[picked] += 1
        # SlotType.CORE 고정 — score_candidates 가 ranking 위해 본 row 다시 받으면 처리.
        # 단 본 함수는 CandidateRow 만 반환 — caller (engine) 가 score_candidates 적용 +
        # ScoredCandidate 로 변환.
        filled.append(top_doc)
    return filled


def select_daily_adjacent_csos(
    user_id: UUID, neighbor_csos: list[UUID], count: int = 3
) -> list[UUID]:
    """(C-62 후속 round2, 2026-05-26) day seed 기반 deterministic 3 random select.

    orchestrator (collection 단계) + engine (dashboard build 단계) 양쪽이 호출 →
    같은 day_seed → 같은 sample → 일관성 보장.
    Collection 이 본 함수로 select 한 cso 들을 LLM 검색 input 으로 사용 →
    그 cso 산하 DocumentTopic 매핑 채움 → dashboard 가 같은 cso 의 doc pick.

    Args:
        user_id: deterministic seed 의 일부.
        neighbor_csos: trace cso 의 1-hop 이웃 list (trace path 제외). caller 가
                       정렬된 list 로 전달 (정렬 일관성 자체는 caller 책임).
        count: select 개수. default 3 = adjacent slot target.
    """
    if not neighbor_csos:
        return []
    rng = random.Random(_day_seed(user_id))
    n = min(len(neighbor_csos), count)
    return rng.sample(neighbor_csos, n)


async def fill_adjacent_slots_via_softmax(
    db: AsyncSession,
    user_id: UUID,
    target_adjacent: int,
    adjacent_csos: list[UUID],
    cfg: CoreSlotSoftmaxConfig,
) -> list[CandidateRow]:
    """(C-62 후속, 2026-05-26) Adjacent slot per-slot node softmax.

    Core 와 동일 정책 — pool 만 다름:
    - Core pool   = active traces (trace 단위 softmax)
    - Adjacent pool = trace tail 1-hop 이웃 중 trace path 안 들어 있는 cso 들 중
                      **doc 매핑 있는 cso** 만 sample 대상.

    각 슬롯 softmax(uniform weight, temperature 동일) sample → 그 cso path 산하 top
    doc (recommendation_score 정렬). soft cap max_per_trace=1 효과.

    Args:
        adjacent_csos: caller (engine) 가 `trav_queries.get_adjacent_topics(hops=1)` 로
                       이미 trace path 제외한 1-hop 이웃 list 전달.
    """
    if target_adjacent <= 0 or not adjacent_csos:
        return []
    # (C-62 후속 round2, 2026-05-26) collection orchestrator 가 같은 day_seed 로 select
    # 한 cso 들을 미리 LLM 검색 input 으로 사용 → 본 select 와 일관된 cso 가 doc 매핑
    # 보유. doc 부재 시 _query_top_doc_for_trace 가 None 반환 → 슬롯 미달은 FR-43 가 처리.
    sampled_csos = select_daily_adjacent_csos(
        user_id, adjacent_csos, count=target_adjacent
    )
    if not sampled_csos:
        return []
    rng = random.Random(_day_seed(user_id))
    # uniform weight 1.0 — softmax 가 균등 분포 sampling. soft cap 으로 자연 distinct.
    weights: list[tuple[UUID, float]] = [(c, 1.0) for c in sampled_csos]
    pick_count: dict[UUID, int] = {c: 0 for c in sampled_csos}
    used_doc_ids: set[UUID] = set()
    filled: list[CandidateRow] = []
    for _slot_idx in range(target_adjacent):
        available = [
            (c, w) for (c, w) in weights if pick_count[c] < cfg.max_per_trace
        ]
        if not available:
            break
        picked = _softmax_sample(available, rng, temperature=cfg.temperature)
        if picked is None:
            break
        top_doc = await _query_top_doc_for_trace(db, user_id, [picked], used_doc_ids)
        if top_doc is None:
            # 본 cso 산하 통과 가능 doc 고갈 — cap 강제 도달 마킹 (다음 슬롯 sampling 에서 제외).
            pick_count[picked] = cfg.max_per_trace
            continue
        used_doc_ids.add(top_doc.document_id)
        pick_count[picked] += 1
        filled.append(top_doc)
    return filled


def select_daily_discovery_csos(
    user_id: UUID, candidate_csos: list[UUID], count: int = 3
) -> list[UUID]:
    """(C-70, 2026-05-26) day_seed 기반 deterministic discovery 영역 cso sample.

    cold_start orchestrator 의 `_collect_discovery_documents` 가 caller —
    사용자 선택 외 cluster 의 cso_seed_topic_id list 받아 day_seed 로 3 sample.
    select_daily_adjacent_csos 와 별도 seed salt 사용 (`discovery` 접미) — 같은 user
    같은 날 adjacent / discovery sample 이 서로 다른 결과 보장.

    Args:
        candidate_csos: 사용자 선택 외 cluster_seed_topic_id list (BroadInterest -
                        boost trace path 위 cluster). caller 가 정렬된 list 전달.
        count: sample 개수. default 3 = discovery slot prefetch 기본.
    """
    if not candidate_csos:
        return []
    # adjacent 와 다른 seed — 같은 날 같은 user 라도 adjacent/discovery 결과 분리.
    rng = random.Random(_day_seed(user_id) + 1)  # +1 salt — adjacent 와 다른 stream
    n = min(len(candidate_csos), count)
    return rng.sample(candidate_csos, n)


__all__ = [
    "_day_seed",
    "_softmax_sample",
    "fill_adjacent_slots_via_softmax",
    "fill_core_slots_via_softmax",
    "select_daily_adjacent_csos",
    "select_daily_discovery_csos",
]
