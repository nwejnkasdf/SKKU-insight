"""ranking — recommendation-ranking.md §랭킹 점수.

`score(d, u) = topic_match · w_match + freshness · w_fresh + trust · w_trust`

- topic_match(d, u) = max over (cso, leaf) ∈ d.topics of `bucket_score(u, topic) x topic_confidence`
- freshness(d): hours_since_publish 기준 선형 감쇠 (recommendation.toml [freshness])
  - 24h 이내 1.0
  - 7d 이상 0.5
  - 그 사이 선형 보간
- trust(s): source.trust_level → [trust_level_weights]
- bucket_score: UserInterestState.bucket 으로부터 (high=1.0/medium=0.7/low=0.4/neutral=0.2)
  - state row 없으면 neutral fallback (cold-start 직후 등 state_index miss)

NFR-06: topic_match 1순위 정렬 기준. 동률은 freshness, trust 순.

§11.#3 방어: emerging quota race — leaf_status 컬럼 함께 fetch (candidates.py) → ranking
단계에서 in-memory partition (emerging vs active). 별도 SQL 호출 X.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.contracts import InterestBucket
from app.db.models import UserInterestState
from app.interest.bucket import bucket_for
from app.interest.config_loader import InterestParams

from .candidates import CandidateRow
from .config_loader import (
    BucketScoreWeights,
    FreshnessConfig,
    RankingWeights,
    TrustLevelWeights,
)

# state_index 의 key — (cso_topic_id, leaf_topic_id) tuple. cso-only / leaf-only / pair.
StateKey = tuple[UUID | None, UUID | None]


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """ranking 후 dedup 된 1 document — topic_match 최대 (cso, leaf) 매핑 유지."""

    document_id: UUID
    title: str
    source_id: UUID
    source_name: str
    source_type: str
    trust_level: str
    published_at: datetime | None
    cso_topic_id: UUID | None
    leaf_topic_id: UUID | None
    leaf_status: str | None
    leaf_label: str | None
    cso_label: str | None
    topic_confidence: float
    topic_match: float
    freshness: float
    trust: float
    score: float


def _bucket_score_for_state(
    state: UserInterestState | None,
    params: InterestParams,
    bucket_weights: BucketScoreWeights,
) -> float:
    """UserInterestState → bucket → score weight. state=None 시 neutral fallback."""
    if state is None:
        return bucket_weights.neutral
    bucket = bucket_for(state.long_score, state.short_score, params)
    if bucket == InterestBucket.HIGH:
        return bucket_weights.high
    if bucket == InterestBucket.MEDIUM:
        return bucket_weights.medium
    if bucket == InterestBucket.LOW:
        return bucket_weights.low
    return bucket_weights.neutral


def _topic_match(
    row: CandidateRow,
    state_index: Mapping[StateKey, UserInterestState],
    params: InterestParams,
    bucket_weights: BucketScoreWeights,
) -> float:
    """단일 row 의 topic_match — bucket_score x topic_confidence.

    state_index lookup: leaf row 가 있으면 (cso, leaf) 또는 (None, leaf) 시도. 없으면 cso-only.
    state 부재 시 neutral fallback (cold-start 직후 케이스).
    """
    state: UserInterestState | None = None
    # 우선순위: pair (cso, leaf) → leaf-only → cso-only.
    if row.cso_topic_id is not None and row.leaf_topic_id is not None:
        state = state_index.get((row.cso_topic_id, row.leaf_topic_id))
        if state is None:
            state = state_index.get((None, row.leaf_topic_id))
        if state is None:
            state = state_index.get((row.cso_topic_id, None))
    elif row.leaf_topic_id is not None:
        state = state_index.get((None, row.leaf_topic_id))
    elif row.cso_topic_id is not None:
        state = state_index.get((row.cso_topic_id, None))
    bucket_score = _bucket_score_for_state(state, params, bucket_weights)
    return bucket_score * row.topic_confidence


def _freshness(
    published_at: datetime | None, freshness_cfg: FreshnessConfig
) -> float:
    """recommendation.toml [freshness] 선형 감쇠.

    24h 이내 1.0, 7d 이상 floor (0.5). 사이 선형 보간.
    published_at None 이면 floor 값 (오래된 문서 취급).
    Document.published_at 기준 wallclock — user.active_day_counter 무관.
    """
    if published_at is None:
        return freshness_cfg.fresh_floor_value
    now = datetime.now(UTC)
    if published_at.tzinfo is None:
        # naive datetime → UTC 로 간주
        published_at = published_at.replace(tzinfo=UTC)
    hours = (now - published_at).total_seconds() / 3600.0
    full_hours = freshness_cfg.fresh_full_hours
    floor_hours = freshness_cfg.fresh_floor_after_wallclock_days * 24
    floor = freshness_cfg.fresh_floor_value
    if hours <= full_hours:
        return 1.0
    if hours >= floor_hours:
        return floor
    # 선형 보간 — full_hours 에서 1.0, floor_hours 에서 floor
    span = floor_hours - full_hours
    if span <= 0:
        return floor
    progress = (hours - full_hours) / span
    return 1.0 - progress * (1.0 - floor)


def score_candidates(
    rows: list[CandidateRow],
    state_index: Mapping[StateKey, UserInterestState],
    params: InterestParams,
    ranking_weights: RankingWeights,
    freshness_cfg: FreshnessConfig,
    trust_cfg: TrustLevelWeights,
    bucket_weights: BucketScoreWeights,
) -> list[ScoredCandidate]:
    """rows → ScoredCandidate list. document_id 별 max(topic_match) dedup.

    같은 document 가 여러 (cso, leaf) 매핑으로 multi-row 반환된 경우 max(topic_match)
    선택 + 그 row 의 (cso, leaf, leaf_status, leaf_label) 유지.

    return: score DESC 정렬 list.
    """
    if not rows:
        return []
    # 1. row 별 점수 계산.
    scored: list[ScoredCandidate] = []
    for row in rows:
        tm = _topic_match(row, state_index, params, bucket_weights)
        fresh = _freshness(row.published_at, freshness_cfg)
        trust = trust_cfg.lookup(row.trust_level)
        score = (
            tm * ranking_weights.w_match
            + fresh * ranking_weights.w_fresh
            + trust * ranking_weights.w_trust
        )
        scored.append(
            ScoredCandidate(
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
                topic_confidence=row.topic_confidence,
                topic_match=tm,
                freshness=fresh,
                trust=trust,
                score=score,
            )
        )
    # 2. document_id 별 max(topic_match) dedup. 동률 시 max(score) 선택.
    by_doc: dict[UUID, ScoredCandidate] = {}
    for sc in scored:
        existing = by_doc.get(sc.document_id)
        if existing is None:
            by_doc[sc.document_id] = sc
            continue
        # 1순위: topic_match. 2순위: score 전체. 3순위: freshness.
        if sc.topic_match > existing.topic_match:
            by_doc[sc.document_id] = sc
        elif sc.topic_match == existing.topic_match:
            if sc.score > existing.score:
                by_doc[sc.document_id] = sc
            elif sc.score == existing.score and sc.freshness > existing.freshness:
                by_doc[sc.document_id] = sc
    # 3. score DESC 정렬 (동률은 freshness, trust 순).
    result = sorted(
        by_doc.values(),
        key=lambda c: (c.topic_match, c.score, c.freshness, c.trust),
        reverse=True,
    )
    return result


__all__ = [
    "ScoredCandidate",
    "StateKey",
    "score_candidates",
]
