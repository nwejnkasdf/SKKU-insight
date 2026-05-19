"""A8-v2 daily user_profile cron 의 핵심 함수 4종.

핵심 흐름:
1. `fetch_profile_llm_input` — DB 조회 → ProfileLLMInput dataclass.
2. `generate_profile_payload` — LLM 호출 + Pydantic 검증 + CSO 매핑 가드.
3. `upsert_user_profile` — 단일 INSERT ON CONFLICT DO UPDATE.
4. `get_user_profile` — engine.build_dashboard 의 discovery 분기 fetch (Redis cache).

Anti-pattern 회피 (decisions.md §15 + A6/A7/A8 lesson):
- cache-before-commit — caller (worker._run) 가 db.commit() 후 SETEX.
- atomic UPSERT 단일 SQL — A6 C-01 패턴.
- LLM hallucination (CSO 그래프 부재 ID) — bridge_cso_topic_id 매핑 가드.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import networkx as nx
import redis.asyncio as aioredis
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func as sql_func

from app.contracts import (
    LeafTopicStatus,
    RedisKey,
)
from app.db.models import (
    CSOTopic,
    DocumentTopic,
    DynamicLeafTopic,
    HiddenDocument,
    NotInterestedTopic,
    SavedDocument,
    User,
    UserCSOTraversal,
    UserInterestState,
    UserProfile,
)
from app.llm_provider.protocol import (
    ChatMessage,
    LLMProvider,
    ProviderError,
)
from app.profile.config_loader import ProfileGeneratorConfig
from app.profile.prompt_builder import (
    build_system_prompt,
    build_user_prompt,
    to_input_payload,
)
from app.profile.schemas import (
    ActiveTraceSummary,
    ArchivedTraceSummary,
    CSOTopicCandidate,
    InterestStateSummary,
    ProfileLLMInput,
    UserProfilePayload,
)
from app.traversal import queries as trav_queries

logger = logging.getLogger(__name__)


_RECENT_SAVED_HIDDEN_LIMIT = 5
_NOT_INTERESTED_LIMIT = 10
_INTEREST_STATE_TOP_N = 20


async def fetch_profile_llm_input(
    db: AsyncSession,
    user: User,
    *,
    archive_score_tail_min: float,
    input_archive_max: int,
    cso_graph: nx.DiGraph,
) -> ProfileLLMInput:
    """LLM input 묶음 fetch — active + archived traces + state + 명시 피드백 + 후보 풀.

    - active_traces: `get_active_traces` (last_activity DESC). 모든 path label 풀어줌.
    - archived_traces: `get_archived_traces_with_score(score_tail_min, limit)` 적용.
    - top_interest_states: long_score DESC top 20.
    - recent_saved/hidden_topic_labels: 최근 14 active day SavedDocument / HiddenDocument
      의 DocumentTopic CSO label 상위 N (단순 SQL COUNT — 정확도보다 신호 존재만).
    - not_interested_topic_labels: NotInterestedTopic 의 CSO label.
    - cso_candidate_pool: active path 노드 + 1-hop 이웃 + archived path 노드 — LLM 이
      bridge_cso_topic_id 선택 시 본 풀에서만 골라야 함.
    """
    active_orm = await trav_queries.get_active_traces(db, user.user_id)
    archived_orm = await trav_queries.get_archived_traces_with_score(
        db,
        user.user_id,
        score_tail_min=archive_score_tail_min,
        limit=input_archive_max,
    )
    label_lookup = await _build_cso_label_lookup(
        db, _collect_cso_ids(active_orm, archived_orm)
    )

    active_summaries: list[ActiveTraceSummary] = []
    for tr in active_orm:
        labels = [label_lookup.get(cid, str(cid)) for cid in tr.path]
        active_summaries.append(
            ActiveTraceSummary(
                trace_id=tr.trace_id,
                path_labels=labels,
                path_cso_topic_ids=list(tr.path),
                score_tail=float(tr.score_tail),
                last_activity_active_day=int(tr.last_activity_active_day),
            )
        )

    archived_summaries: list[ArchivedTraceSummary] = []
    for tr in archived_orm:
        labels = [label_lookup.get(cid, str(cid)) for cid in tr.path]
        archived_summaries.append(
            ArchivedTraceSummary(
                trace_id=tr.trace_id,
                path_labels=labels,
                path_cso_topic_ids=list(tr.path),
                score_tail_at_archive=float(tr.score_tail),
                last_activity_active_day=int(tr.last_activity_active_day),
                archived_at_active_day=int(tr.last_activity_active_day),
            )
        )

    top_states = await _fetch_top_interest_states(db, user.user_id, label_lookup)
    saved_labels = await _fetch_saved_topic_labels(
        db, user.user_id, limit=_RECENT_SAVED_HIDDEN_LIMIT
    )
    hidden_labels = await _fetch_hidden_topic_labels(
        db, user.user_id, limit=_RECENT_SAVED_HIDDEN_LIMIT
    )
    not_interested_labels = await _fetch_not_interested_labels(
        db, user.user_id, limit=_NOT_INTERESTED_LIMIT
    )
    candidate_pool = _build_cso_candidate_pool(
        active_orm, archived_orm, label_lookup, cso_graph
    )

    return ProfileLLMInput(
        user_active_day_counter=int(user.active_day_counter),
        active_traces=active_summaries,
        archived_traces=archived_summaries,
        top_interest_states=top_states,
        recent_saved_topic_labels=saved_labels,
        recent_hidden_topic_labels=hidden_labels,
        not_interested_topic_labels=not_interested_labels,
        cso_candidate_pool=candidate_pool,
    )


def _collect_cso_ids(
    active: list[UserCSOTraversal], archived: list[UserCSOTraversal]
) -> set[UUID]:
    ids: set[UUID] = set()
    for trace in (*active, *archived):
        for cid in trace.path:
            ids.add(cid)
    return ids


async def _build_cso_label_lookup(
    db: AsyncSession, cso_ids: set[UUID]
) -> dict[UUID, str]:
    if not cso_ids:
        return {}
    stmt = select(CSOTopic.cso_topic_id, CSOTopic.label).where(
        CSOTopic.cso_topic_id.in_(cso_ids)
    )
    rows = (await db.execute(stmt)).all()
    return {row.cso_topic_id: row.label for row in rows}


async def _fetch_top_interest_states(
    db: AsyncSession,
    user_id: UUID,
    label_lookup: dict[UUID, str],
) -> list[InterestStateSummary]:
    stmt = (
        select(UserInterestState)
        .where(UserInterestState.user_id == user_id)
        .order_by(UserInterestState.long_score.desc())
        .limit(_INTEREST_STATE_TOP_N)
    )
    rows = (await db.execute(stmt)).scalars().all()
    summaries: list[InterestStateSummary] = []
    for state in rows:
        if state.cso_topic_id is not None:
            label = label_lookup.get(state.cso_topic_id) or str(state.cso_topic_id)
        elif state.leaf_topic_id is not None:
            leaf_label = await _fetch_leaf_label(db, state.leaf_topic_id)
            label = leaf_label or str(state.leaf_topic_id)
        else:
            continue
        summaries.append(
            InterestStateSummary(
                cso_topic_id=state.cso_topic_id,
                leaf_topic_id=state.leaf_topic_id,
                label=label,
                long_score=float(state.long_score),
                short_score=float(state.short_score),
            )
        )
    return summaries


async def _fetch_leaf_label(db: AsyncSession, leaf_id: UUID) -> str | None:
    stmt = select(DynamicLeafTopic.label).where(
        DynamicLeafTopic.leaf_topic_id == leaf_id,
        DynamicLeafTopic.status.in_(
            [
                LeafTopicStatus.ACTIVE.value,
                LeafTopicStatus.EMERGING.value,
                LeafTopicStatus.STALE.value,
            ]
        ),
    )
    result = (await db.execute(stmt)).scalar_one_or_none()
    return None if result is None else str(result)


async def _fetch_saved_topic_labels(
    db: AsyncSession, user_id: UUID, *, limit: int
) -> list[str]:
    """SavedDocument 가 매핑된 CSO 토픽 라벨 상위 N (COUNT DESC). LLM 입력 신호."""
    stmt = (
        select(CSOTopic.label)
        .join(
            DocumentTopic,
            DocumentTopic.cso_topic_id == CSOTopic.cso_topic_id,
        )
        .join(
            SavedDocument,
            SavedDocument.document_id == DocumentTopic.document_id,
        )
        .where(SavedDocument.user_id == user_id)
        .group_by(CSOTopic.label)
        .order_by(sql_func.count().desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [str(row.label) for row in rows]


async def _fetch_hidden_topic_labels(
    db: AsyncSession, user_id: UUID, *, limit: int
) -> list[str]:
    """HiddenDocument 의 토픽 라벨 상위 N — likely_dislikes 신호."""
    stmt = (
        select(CSOTopic.label)
        .join(
            DocumentTopic,
            DocumentTopic.cso_topic_id == CSOTopic.cso_topic_id,
        )
        .join(
            HiddenDocument,
            HiddenDocument.document_id == DocumentTopic.document_id,
        )
        .where(HiddenDocument.user_id == user_id)
        .group_by(CSOTopic.label)
        .order_by(sql_func.count().desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [str(row.label) for row in rows]


async def _fetch_not_interested_labels(
    db: AsyncSession, user_id: UUID, *, limit: int
) -> list[str]:
    stmt = (
        select(CSOTopic.label)
        .join(
            NotInterestedTopic,
            NotInterestedTopic.cso_topic_id == CSOTopic.cso_topic_id,
        )
        .where(NotInterestedTopic.user_id == user_id)
        .order_by(NotInterestedTopic.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [str(row.label) for row in rows]


def _build_cso_candidate_pool(
    active: list[UserCSOTraversal],
    archived: list[UserCSOTraversal],
    label_lookup: dict[UUID, str],
    cso_graph: nx.DiGraph,
) -> list[CSOTopicCandidate]:
    """LLM 이 bridge_cso_topic_id 선택 시 사용할 후보 풀 — active + archive path 의
    모든 노드 + active path 끝 노드의 1-hop 이웃.

    label_lookup 에 없는 ID 는 cso_graph 의 node attribute 에서 label 시도, 그것도
    없으면 str(uuid). cso_graph 는 networkx DiGraph.
    """
    seen: set[UUID] = set()
    result: list[CSOTopicCandidate] = []
    for trace in (*active, *archived):
        for cid in trace.path:
            if cid in seen:
                continue
            seen.add(cid)
            result.append(
                CSOTopicCandidate(
                    cso_topic_id=cid,
                    label=label_lookup.get(cid) or str(cid),
                )
            )
    # 1-hop 이웃 (active path 끝 only) — adjacent CSO label 도 후보 풀에.
    for trace in active:
        if not trace.path:
            continue
        tail = trace.path[-1]
        if tail not in cso_graph:
            continue
        for neighbor in cso_graph.successors(tail):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            attrs = cso_graph.nodes[neighbor]
            label = attrs.get("label") or label_lookup.get(neighbor) or str(neighbor)
            result.append(
                CSOTopicCandidate(cso_topic_id=neighbor, label=str(label))
            )
        for predecessor in cso_graph.predecessors(tail):
            if predecessor in seen:
                continue
            seen.add(predecessor)
            attrs = cso_graph.nodes[predecessor]
            label = attrs.get("label") or label_lookup.get(predecessor) or str(predecessor)
            result.append(
                CSOTopicCandidate(cso_topic_id=predecessor, label=str(label))
            )
    return result


async def generate_profile_payload(
    provider: LLMProvider,
    cso_graph: nx.DiGraph,
    *,
    llm_input: ProfileLLMInput,
    config: ProfileGeneratorConfig,
    user_id: UUID,
) -> UserProfilePayload | None:
    """LLM 호출 + Pydantic 검증 + bridge_cso_topic_id ∈ cso_graph 매핑 가드.

    실패 모드 (None 반환):
    - ProviderError (LLM 호출 실패 / timeout / parse error)
    - parsed_json 부재 (provider 가 json mode 안 줌)
    - Pydantic ValidationError (schema 위반)

    위반 candidate 만 제거 (전체 응답은 보존):
    - fusion_candidates 의 bridge_cso_topic_id ∉ cso_graph → 본 candidate 만 drop
    - deepening_seeds / broadening_seeds 의 cso_topic_id ∉ cso_graph → drop

    Anti-pattern: LLM hallucination — A7 R3 lesson + decisions.md §15 결정 매트릭스.
    """
    messages = [
        ChatMessage(role="system", content=build_system_prompt(config.generator_version)),
        ChatMessage(
            role="user",
            content=build_user_prompt(to_input_payload(llm_input)),
        ),
    ]
    try:
        response = await provider.complete(
            messages,
            model_slot="high",
            response_format="json",
            user_id=str(user_id),
        )
    except ProviderError as exc:
        logger.warning(
            "user_profile LLM provider error user=%s err=%s", user_id, exc
        )
        return None

    parsed = response.parsed_json
    if not isinstance(parsed, dict):
        logger.warning(
            "user_profile LLM response missing parsed_json user=%s", user_id
        )
        return None
    try:
        payload = UserProfilePayload.model_validate(parsed)
    except ValidationError as exc:
        logger.warning(
            "user_profile LLM payload schema violation user=%s err=%s",
            user_id,
            exc.errors()[:3],
        )
        return None

    valid_fusion = [
        candidate
        for candidate in payload.fusion_candidates
        if candidate.bridge_cso_topic_id in cso_graph
    ]
    valid_deepening = [
        seed for seed in payload.deepening_seeds if seed.cso_topic_id in cso_graph
    ]
    valid_broadening = [
        seed for seed in payload.broadening_seeds if seed.cso_topic_id in cso_graph
    ]
    return payload.model_copy(
        update={
            "fusion_candidates": valid_fusion,
            "deepening_seeds": valid_deepening,
            "broadening_seeds": valid_broadening,
        }
    )


async def upsert_user_profile(
    db: AsyncSession,
    *,
    user_id: UUID,
    payload: UserProfilePayload,
    generator_version: str,
) -> None:
    """단일 INSERT ON CONFLICT DO UPDATE — A6 _atomic_upsert_interest_state 패턴 단순화.

    PK = user_id 단일이라 partial unique 분기 불필요. caller (worker._run) 가 commit.
    """
    fusion_serialized = [c.model_dump(mode="json") for c in payload.fusion_candidates]
    deepening_serialized = [s.model_dump(mode="json") for s in payload.deepening_seeds]
    broadening_serialized = [s.model_dump(mode="json") for s in payload.broadening_seeds]
    stmt = pg_insert(UserProfile).values(
        user_id=user_id,
        recent_signals_summary=payload.recent_signals_summary,
        persistent_tendencies_summary=payload.persistent_tendencies_summary,
        likely_dislikes_summary=payload.likely_dislikes_summary,
        fusion_candidates=fusion_serialized,
        deepening_seeds=deepening_serialized,
        broadening_seeds=broadening_serialized,
        generator_version=generator_version,
        generated_at=sql_func.now(),
        updated_at=sql_func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id"],
        set_={
            "recent_signals_summary": payload.recent_signals_summary,
            "persistent_tendencies_summary": payload.persistent_tendencies_summary,
            "likely_dislikes_summary": payload.likely_dislikes_summary,
            "fusion_candidates": fusion_serialized,
            "deepening_seeds": deepening_serialized,
            "broadening_seeds": broadening_serialized,
            "generator_version": generator_version,
            "generated_at": sql_func.now(),
            "updated_at": sql_func.now(),
        },
    )
    await db.execute(stmt)


async def get_user_profile(
    db: AsyncSession,
    redis: aioredis.Redis,
    user_id: UUID,
    *,
    cache_ttl_seconds: int,
) -> UserProfile | None:
    """Redis cache → miss 시 DB lookup → SETEX. engine.build_dashboard 가 호출.

    cache miss 시: SELECT user_profile WHERE user_id=:user_id → None 가능 (cron 미실행).
    cache hit 시: JSON deserialize → ORM 인스턴스 재구성 (read-only, attached X).
    """
    cache_key = RedisKey.user_profile_cache(user_id)
    cached = await redis.get(cache_key)
    if cached:
        try:
            data = json.loads(cached)
            return _hydrate_user_profile_from_cache(data)
        except (ValueError, TypeError, KeyError):
            logger.warning(
                "user_profile cache corrupt user=%s key=%s", user_id, cache_key
            )
            await redis.delete(cache_key)
    stmt = select(UserProfile).where(UserProfile.user_id == user_id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if profile is None:
        return None
    serialized = json.dumps(_serialize_user_profile_for_cache(profile))
    await redis.setex(cache_key, cache_ttl_seconds, serialized)
    return profile


def _serialize_user_profile_for_cache(profile: UserProfile) -> dict[str, Any]:
    return {
        "user_id": str(profile.user_id),
        "recent_signals_summary": profile.recent_signals_summary,
        "persistent_tendencies_summary": profile.persistent_tendencies_summary,
        "likely_dislikes_summary": profile.likely_dislikes_summary,
        "fusion_candidates": profile.fusion_candidates,
        "deepening_seeds": profile.deepening_seeds,
        "broadening_seeds": profile.broadening_seeds,
        "generator_version": profile.generator_version,
        "generated_at": profile.generated_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
    }


def _hydrate_user_profile_from_cache(data: dict[str, Any]) -> UserProfile:
    profile = UserProfile(
        user_id=UUID(data["user_id"]),
        recent_signals_summary=data.get("recent_signals_summary"),
        persistent_tendencies_summary=data.get("persistent_tendencies_summary"),
        likely_dislikes_summary=data.get("likely_dislikes_summary"),
        fusion_candidates=data.get("fusion_candidates") or [],
        deepening_seeds=data.get("deepening_seeds") or [],
        broadening_seeds=data.get("broadening_seeds") or [],
        generator_version=data["generator_version"],
        generated_at=datetime.fromisoformat(data["generated_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )
    return profile


__all__ = [
    "fetch_profile_llm_input",
    "generate_profile_payload",
    "get_user_profile",
    "upsert_user_profile",
]
