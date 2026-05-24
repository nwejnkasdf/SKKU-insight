"""수집 파이프라인 — v13 라운드 A4 Topic-driven Pivot 의 심장부.

run_collection_for_user(user_id):
  1. SETNX lock:collection:{user_id} TTL=7200s → 실패 시 CollectionAlreadyRunning
  2. INSERT CollectionJob(source_id=<llm_search sentinel>, status=RUNNING, started_at)
  3. leaves = resolve_active_leaves(user_id)
       ├─ DynamicLeafTopic WHERE user_id AND status='active'
       ├─ active trace tail topics + 1-hop adjacent topics
       └─ Q2-A fallback: BroadInterest 12 중 hash(user_id) 로 1 seed + 1-hop adjacent 2
  4. trace_json = build_trace_json(user_id) (UserCSOTraversal 있으면, 없으면 fallback dict)
  5. existing_keys = load_existing_dedup_keys(user_id, since=30d)
  6. for leaf:
       try: search → dedup → INSERT Document + DocumentTopic → commit per leaf
       except (ProviderError, LLMBudgetExceeded, ...): rollback + failure summary
  7. UPDATE CollectionJob (모두 실패 FAILED, 일부 실패 SUCCEEDED+summary, 전부 성공 SUCCEEDED)
  8. DELETE lock

deterministic_jitter_seconds(user_id, today, cap=300):
  - SHA256(f"{user_id}:{today.isoformat()}") → int(:8byte) % cap

clickbait placeholder: settings.CLICKBAIT_ENABLED=true 일 때 A5 호출 위치 (현재 pass).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from sqlalchemy import func as sa_func
from sqlalchemy import or_, select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection import dedup as dedup_module
from app.collection import llm_search
from app.config import get_settings
from app.contracts import (
    CollectionJobStatus,
    ContentType,
    LeafTopicStatus,
    RedisKey,
    TraversalStatus,
)
from app.db.models import (
    BroadInterest,
    CollectionJob,
    CSOTopic,
    CSOTopicParent,
    Document,
    DocumentTopic,
    DynamicLeafTopic,
    DynamicLeafTopicCSOTopic,
    Source,
    UserCSOTraversal,
)
from app.llm_provider.protocol import (
    LLMBudgetExceeded,
    LLMProvider,
    ProviderError,
    SearchResult,
)

logger = logging.getLogger(__name__)


# alembic 0003 의 sentinel UUID 와 동일 이름 lookup 사용 (UUID 자체 hardcode 회피).
LLM_SEARCH_SENTINEL_NAME = "llm_search"
_LOCK_TTL_SECONDS = 7200
_DEDUP_WINDOW_DAYS = 30
_TRACE_COLLECTION_LIMIT = 5
_FALLBACK_LEAF_LIMIT = 3
_DEFAULT_TOP_N = 10
_JITTER_CAP_SECONDS = 300
_FAILURE_REASON_MAX = 2000


class CollectionAlreadyRunning(Exception):
    """동일 사용자에 대해 이미 다른 수집 잡이 진행 중 (Redis lock)."""


@dataclass(slots=True, frozen=True)
class LeafTarget:
    """수집 단위. DynamicLeafTopic 또는 fallback substitute."""

    leaf_label: str
    parent_cso_topic_id: UUID
    leaf_topic_id: UUID | None  # fallback 경로는 None


@dataclass(slots=True)
class CollectionJobResult:
    """run_collection_for_user 반환값. router/worker 가 로깅·응답에 사용."""

    job_id: UUID
    status: CollectionJobStatus
    leaves_processed: int = 0
    documents_inserted: int = 0
    failures: list[str] = field(default_factory=list)


def deterministic_jitter_seconds(
    user_id: UUID, today: date, *, cap: int = _JITTER_CAP_SECONDS
) -> int:
    """deterministic jitter — 같은 (user, day) → 같은 sleep. v13 사용자 결정."""
    payload = f"{user_id}:{today.isoformat()}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big") % cap


async def _get_llm_search_source_id(db: AsyncSession) -> UUID:
    """alembic 0003 시드한 sentinel UUID lookup. 1회 호출 — 캐시 불필요."""
    stmt = select(Source.source_id).where(Source.name == LLM_SEARCH_SENTINEL_NAME)
    source_id = (await db.execute(stmt)).scalar_one_or_none()
    if source_id is None:
        raise RuntimeError(
            f"sentinel source '{LLM_SEARCH_SENTINEL_NAME}' 미시드 — alembic 0003 적용 필요"
        )
    return source_id


async def resolve_active_leaves(
    db: AsyncSession, user_id: UUID
) -> list[LeafTarget]:
    """수집 대상 leaf/topic 결정.

    1. DynamicLeafTopic WHERE user_id AND status='active'
       → 매핑된 cso_topic_id (DynamicLeafTopicCSOTopic) 중 첫 번째를 parent 로 사용.
    2. 비면 active trace path 끝 노드 + 1-hop adjacent 를 pseudo leaf 로 사용.
       A7 leaf 생성 전에도 실제 관심 trace 기준으로 수집되게 하는 A4/A8 demo bridge.
    3. 그래도 비면 fallback: BroadInterest 12 행 중 hash(user_id) % 12 → 1 seed
       + cso_topic_parent 1-hop adjacent 2 개 (deterministic hash 로 선택)
       → 최대 3 LeafTarget.
    4. 그래도 비면 빈 list 반환 → orchestrator SKIPPED.
    """
    leaf_stmt = select(DynamicLeafTopic).where(
        DynamicLeafTopic.user_id == user_id,
        DynamicLeafTopic.status == LeafTopicStatus.ACTIVE.value,
    )
    leaf_rows = list((await db.execute(leaf_stmt)).scalars().all())
    if leaf_rows:
        leaf_ids = [lf.leaf_topic_id for lf in leaf_rows]
        map_stmt = select(
            DynamicLeafTopicCSOTopic.leaf_topic_id,
            DynamicLeafTopicCSOTopic.cso_topic_id,
        ).where(DynamicLeafTopicCSOTopic.leaf_topic_id.in_(leaf_ids))
        parent_map: dict[UUID, UUID] = {}
        for row in await db.execute(map_stmt):
            existing = parent_map.get(row.leaf_topic_id)
            if existing is None or row.cso_topic_id < existing:
                parent_map[row.leaf_topic_id] = row.cso_topic_id
        targets: list[LeafTarget] = []
        for lf in leaf_rows:
            parent = parent_map.get(lf.leaf_topic_id)
            if parent is None:
                continue
            targets.append(
                LeafTarget(
                    leaf_label=lf.label,
                    parent_cso_topic_id=parent,
                    leaf_topic_id=lf.leaf_topic_id,
                )
            )
        if targets:
            return targets

    trace_targets = await _resolve_trace_leaves(db, user_id)
    if trace_targets:
        return trace_targets

    return await _resolve_fallback_leaves(db, user_id)


async def _resolve_trace_leaves(
    db: AsyncSession, user_id: UUID
) -> list[LeafTarget]:
    """A7 dynamic leaf 부재 시 active trace 기반 수집 대상 구성.

    docs/sdd/data-flow.md 의 "current + adjacent" 수집 규약을 코드에 맞춘다.
    current 는 각 active trace 의 tail 노드이고, adjacent 는 tail 의 1-hop 이웃 중
    deterministic hash 순서로 일부만 고른다. leaf_topic_id 는 아직 없으므로 NULL.
    """
    trace_stmt = (
        select(UserCSOTraversal)
        .where(
            UserCSOTraversal.user_id == user_id,
            UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
        )
        .order_by(UserCSOTraversal.updated_at.desc())
        .limit(_TRACE_COLLECTION_LIMIT)
    )
    traces = list((await db.execute(trace_stmt)).scalars().all())
    current_ids: list[UUID] = []
    seen: set[UUID] = set()
    for trace in traces:
        if not trace.path:
            continue
        tail_id = trace.path[-1]
        if tail_id in seen:
            continue
        seen.add(tail_id)
        current_ids.append(tail_id)
        if len(current_ids) >= _TRACE_COLLECTION_LIMIT:
            break
    if not current_ids:
        return []

    target_ids = list(current_ids)
    needed = _TRACE_COLLECTION_LIMIT - len(target_ids)
    if needed > 0:
        adjacent_ids = await _select_adjacent_topic_ids(
            db, user_id=user_id, seed_ids=current_ids, limit=needed
        )
        target_ids.extend(adjacent_ids)

    label_map = await _load_cso_labels(db, target_ids)
    targets = [
        LeafTarget(
            leaf_label=label_map.get(topic_id, str(topic_id)),
            parent_cso_topic_id=topic_id,
            leaf_topic_id=None,
        )
        for topic_id in target_ids
    ]
    logger.info(
        "USING_TRACE_FALLBACK user=%s targets=%s",
        user_id,
        [t.leaf_label for t in targets],
    )
    return targets


async def _select_adjacent_topic_ids(
    db: AsyncSession,
    *,
    user_id: UUID,
    seed_ids: list[UUID],
    limit: int,
) -> list[UUID]:
    """seed_ids 의 1-hop adjacent 중 deterministic hash 로 limit 개 선택."""
    if limit <= 0 or not seed_ids:
        return []
    edge_stmt = select(
        CSOTopicParent.cso_topic_id, CSOTopicParent.parent_cso_topic_id
    ).where(
        or_(
            CSOTopicParent.cso_topic_id.in_(seed_ids),
            CSOTopicParent.parent_cso_topic_id.in_(seed_ids),
        )
    )
    seed_set = set(seed_ids)
    adjacent_ids: list[UUID] = []
    for row in await db.execute(edge_stmt):
        candidate_id = (
            row.parent_cso_topic_id
            if row.cso_topic_id in seed_set
            else row.cso_topic_id
        )
        if candidate_id not in seed_set:
            adjacent_ids.append(candidate_id)
    if not adjacent_ids:
        return []

    unique_sorted = sorted(set(adjacent_ids))
    user_hash = int.from_bytes(
        hashlib.sha256(str(user_id).encode()).digest()[:8], "big"
    )
    chosen_ids: list[UUID] = []
    for offset in range(limit):
        if not unique_sorted:
            break
        idx = (user_hash + offset) % len(unique_sorted)
        chosen_ids.append(unique_sorted.pop(idx))
    return chosen_ids


async def _load_cso_labels(
    db: AsyncSession, cso_topic_ids: list[UUID]
) -> dict[UUID, str]:
    """CSO topic label map."""
    if not cso_topic_ids:
        return {}
    label_stmt = select(CSOTopic.cso_topic_id, CSOTopic.label).where(
        CSOTopic.cso_topic_id.in_(cso_topic_ids)
    )
    return {
        row.cso_topic_id: row.label
        for row in await db.execute(label_stmt)
    }


async def _resolve_fallback_leaves(
    db: AsyncSession, user_id: UUID
) -> list[LeafTarget]:
    """USING_ONBOARDING_FALLBACK — BroadInterest 12 중 hash 로 seed + 1-hop adjacent."""
    bi_stmt = select(BroadInterest).order_by(BroadInterest.display_order)
    bi_rows = list((await db.execute(bi_stmt)).scalars().all())
    if not bi_rows:
        return []
    user_hash = int.from_bytes(
        hashlib.sha256(str(user_id).encode()).digest()[:8], "big"
    )
    seed = bi_rows[user_hash % len(bi_rows)]
    seed_cso_id = seed.cso_seed_topic_id

    seed_label_stmt = select(CSOTopic.label).where(
        CSOTopic.cso_topic_id == seed_cso_id
    )
    seed_label = (await db.execute(seed_label_stmt)).scalar_one_or_none() or seed.name
    targets: list[LeafTarget] = [
        LeafTarget(
            leaf_label=str(seed_label),
            parent_cso_topic_id=seed_cso_id,
            leaf_topic_id=None,
        )
    ]
    logger.info("USING_ONBOARDING_FALLBACK user=%s seed=%s", user_id, seed.name)

    adj_stmt = select(
        CSOTopicParent.cso_topic_id, CSOTopicParent.parent_cso_topic_id
    ).where(
        or_(
            CSOTopicParent.cso_topic_id == seed_cso_id,
            CSOTopicParent.parent_cso_topic_id == seed_cso_id,
        )
    )
    adjacent_ids: list[UUID] = []
    for row in await db.execute(adj_stmt):
        adjacent_ids.append(
            row.parent_cso_topic_id
            if row.cso_topic_id == seed_cso_id
            else row.cso_topic_id
        )
    if not adjacent_ids:
        return targets

    unique_sorted = sorted(set(adjacent_ids))
    needed = _FALLBACK_LEAF_LIMIT - len(targets)
    chosen_ids: list[UUID] = []
    for offset in range(needed):
        if not unique_sorted:
            break
        idx = (user_hash + offset) % len(unique_sorted)
        chosen_ids.append(unique_sorted.pop(idx))

    if chosen_ids:
        label_map = await _load_cso_labels(db, chosen_ids)
        for cid in chosen_ids:
            targets.append(
                LeafTarget(
                    leaf_label=label_map.get(cid, str(cid)),
                    parent_cso_topic_id=cid,
                    leaf_topic_id=None,
                )
            )
    return targets


async def build_trace_json(
    db: AsyncSession,
    user_id: UUID,
    *,
    fallback_leaves: list[LeafTarget] | None = None,
) -> dict[str, Any]:
    """LLM 검색 input. UserCSOTraversal active 우선, 없으면 fallback dict."""
    trace_stmt = (
        select(UserCSOTraversal)
        .where(
            UserCSOTraversal.user_id == user_id,
            UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
        )
        .order_by(UserCSOTraversal.updated_at.desc())
        .limit(3)
    )
    rows = list((await db.execute(trace_stmt)).scalars().all())
    if rows:
        all_ids: set[UUID] = set()
        for r in rows:
            all_ids.update(r.path)
        label_stmt = select(CSOTopic.cso_topic_id, CSOTopic.label).where(
            CSOTopic.cso_topic_id.in_(list(all_ids))
        )
        labels_map = {
            row.cso_topic_id: row.label
            for row in await db.execute(label_stmt)
        }
        return {
            "mode": "active_trace",
            "traces": [
                {
                    "trace_id": str(r.trace_id),
                    "path_labels": [labels_map.get(pid, str(pid)) for pid in r.path],
                    "status": r.status,
                }
                for r in rows
            ],
        }
    fallback = fallback_leaves or []
    return {
        "mode": "onboarding_fallback",
        "clusters": [
            {"label": lt.leaf_label, "cso_topic_id": str(lt.parent_cso_topic_id)}
            for lt in fallback
        ],
    }


async def load_existing_dedup_keys(
    db: AsyncSession,
    user_id: UUID,
    *,
    since_days: int = _DEDUP_WINDOW_DAYS,
) -> list[dedup_module.DedupKey]:
    """사용자 최근 N일 Document 의 dedup key + document_id (C-02 fix).

    leaf-매핑 (user_id 격리) 또는 cso-only 매핑 (fallback 경로) 둘 다 포함.
    document_id 는 매칭 시 DocumentTopic upsert 의 FK 로 사용.
    """
    cutoff = datetime.now(UTC) - timedelta(days=since_days)
    stmt = (
        select(
            Document.document_id,
            Document.title,
            Document.url,
            Document.canonical_url,
            Document.doi,
        )
        .join(DocumentTopic, DocumentTopic.document_id == Document.document_id)
        .outerjoin(
            DynamicLeafTopic,
            DynamicLeafTopic.leaf_topic_id == DocumentTopic.leaf_topic_id,
        )
        .where(
            Document.created_at >= cutoff,
            or_(
                DynamicLeafTopic.user_id == user_id,
                DocumentTopic.leaf_topic_id.is_(None),
            ),
        )
        .distinct()
    )
    keys: list[dedup_module.DedupKey] = []
    for row in await db.execute(stmt):
        keys.append(
            dedup_module.make_key(
                SearchResult(
                    title=row.title,
                    url=row.url,
                    canonical_url=row.canonical_url,
                    doi=row.doi,
                    abstract_summary="",
                ),
                document_id=row.document_id,
            )
        )
    return keys


async def run_collection_for_user(
    db: AsyncSession,
    redis: aioredis.Redis,
    provider: LLMProvider,
    user_id: UUID,
    *,
    job_type: str = "daily_collect",
    existing_job_id: UUID | None = None,
) -> CollectionJobResult:
    """단일 user 수집.

    (v13 round 2 S-01) `existing_job_id` 가 있으면 그 row 를 RUNNING 으로 전이
    (service.trigger_run_now 가 queued 로 먼저 INSERT 한 row). 없으면 신규 INSERT.

    (v13 round 2 S-03) 모든 leaf 가 provider 실패면 status=FAILED 마킹 후 re-raise →
    RQ retry trigger. 부분 실패는 SUCCEEDED + summary (정상 종료).
    """
    lock_key = RedisKey.collection_lock(user_id)
    acquired = await redis.set(lock_key, "1", nx=True, ex=_LOCK_TTL_SECONDS)
    if not acquired:
        raise CollectionAlreadyRunning(f"lock held: {lock_key}")

    job_id = existing_job_id if existing_job_id else uuid4()
    job_result = CollectionJobResult(
        job_id=job_id, status=CollectionJobStatus.RUNNING
    )
    try:
        sentinel_id = await _get_llm_search_source_id(db)
        if existing_job_id is not None:
            # queued row UPDATE → RUNNING
            update_stmt = select(CollectionJob).where(
                CollectionJob.job_id == existing_job_id
            )
            job = (await db.execute(update_stmt)).scalar_one_or_none()
            if job is None:
                # service 가 INSERT 한 row 가 사라졌으면 fallback: 신규 INSERT
                job = CollectionJob(
                    job_id=existing_job_id,
                    user_id=user_id,
                    source_id=sentinel_id,
                    job_type=job_type,
                    status=CollectionJobStatus.RUNNING.value,
                    started_at=datetime.now(UTC),
                )
                db.add(job)
            else:
                # (round 3 R2-S02) RQ retry 시 동일 row 재사용 — 이전 시도의 터미널
                # 필드 (finished_at/failure_reason) 초기화 + retry_count 증가. queued
                # 상태에서 처음 RUNNING 전이 시는 retry_count 증가 X.
                if job.status in (
                    CollectionJobStatus.FAILED.value,
                    CollectionJobStatus.SUCCEEDED.value,
                    CollectionJobStatus.SKIPPED.value,
                    CollectionJobStatus.RUNNING.value,
                ):
                    job.retry_count = (job.retry_count or 0) + 1
                job.status = CollectionJobStatus.RUNNING.value
                job.started_at = datetime.now(UTC)
                job.finished_at = None
                job.failure_reason = None
        else:
            job = CollectionJob(
                job_id=job_id,
                user_id=user_id,
                source_id=sentinel_id,
                job_type=job_type,
                status=CollectionJobStatus.RUNNING.value,
                started_at=datetime.now(UTC),
            )
            db.add(job)
        await db.commit()

        leaves = await resolve_active_leaves(db, user_id)
        if not leaves:
            await _finalize_job(
                db,
                job_id,
                status=CollectionJobStatus.SKIPPED,
                failure_reason="no_active_leaves",
            )
            job_result.status = CollectionJobStatus.SKIPPED
            job_result.failures.append("no_active_leaves")
            return job_result

        trace_json = await build_trace_json(db, user_id, fallback_leaves=leaves)
        existing_keys = await load_existing_dedup_keys(db, user_id)

        documents_total = 0
        for leaf in leaves:
            try:
                results = await llm_search.search_for_leaf(
                    provider,
                    trace_json=trace_json,
                    leaf_label=leaf.leaf_label,
                    parent_cso_topic_id=leaf.parent_cso_topic_id,
                    user_id=user_id,
                    top_n=_DEFAULT_TOP_N,
                )
                # (C-02) collapse 가 2 그룹 분리: 신규 INSERT vs 기존 매핑 upsert
                to_insert, to_link = dedup_module.collapse(existing_keys, results)
                inserted_count, new_keys = await _persist_results(
                    db, sentinel_id, leaf, to_insert, to_link
                )
                documents_total += inserted_count
                # 신규 INSERT 된 키만 existing 에 추가 (다음 leaf 와의 dedup 위해).
                # to_link 는 이미 existing 안에 있는 키와 매칭됐으므로 추가 불필요.
                existing_keys.extend(new_keys)
                job_result.leaves_processed += 1
                await db.commit()
            except (ProviderError, LLMBudgetExceeded) as exc:
                await db.rollback()
                msg = f"leaf={leaf.leaf_label}: {type(exc).__name__}: {exc}"
                job_result.failures.append(msg)
                logger.warning("collection leaf failed: %s", msg)
            except Exception as exc:
                await db.rollback()
                msg = f"leaf={leaf.leaf_label}: unexpected {type(exc).__name__}: {exc}"
                job_result.failures.append(msg)
                logger.exception("collection leaf unexpected failure")

        job_result.documents_inserted = documents_total

        if not job_result.failures:
            final_status = CollectionJobStatus.SUCCEEDED
            failure_reason: str | None = None
            should_reraise = False
        elif job_result.leaves_processed == 0:
            # 모든 leaf 실패 — FAILED 마킹 후 RQ retry trigger 위해 re-raise (S-03)
            final_status = CollectionJobStatus.FAILED
            failure_reason = (" | ".join(job_result.failures))[
                :_FAILURE_REASON_MAX
            ]
            should_reraise = True
        else:
            # 부분 실패 — SUCCEEDED + summary (정상 종료, retry X)
            final_status = CollectionJobStatus.SUCCEEDED
            failure_reason = ("partial: " + " | ".join(job_result.failures))[
                :_FAILURE_REASON_MAX
            ]
            should_reraise = False

        await _finalize_job(
            db, job_id, status=final_status, failure_reason=failure_reason
        )
        job_result.status = final_status
        if should_reraise:
            # RQ 가 본 raise 를 catch → retry (max 3, interval [60s,300s,900s]).
            # failure_reason 은 이미 DB 에 저장됨 → retry 가 별도 row 안 만들고
            # 같은 job_id 의 retry_count 만 증가시키도록 worker 가 처리.
            raise ProviderError(failure_reason or "all leaves failed")
        return job_result
    finally:
        await redis.delete(lock_key)


async def _persist_results(
    db: AsyncSession,
    sentinel_source_id: UUID,
    leaf: LeafTarget,
    to_insert: list[SearchResult],
    to_link: list[tuple[SearchResult, UUID]],
) -> tuple[int, list[dedup_module.DedupKey]]:
    """SearchResult 2 그룹 → Document/DocumentTopic 영속.

    (round 2 C-02·C-03 + round 3 R2-C01/C02/S06):
    - to_insert: Document untargeted `on_conflict_do_nothing()` + canonical→doi fallback
      lookup → (doc_id, is_new) 반환. 신규일 때만 inserted_count 증가 (R2-S06 통계 정확).
    - to_link: 이미 알려진 기존 document_id 와 DocumentTopic upsert.

    DocumentTopic 는 `DO UPDATE SET confidence = greatest(...)` (R2-S04 staleness 차단).

    반환: (신규 INSERT 된 Document 수, 신규 DedupKey list — orchestrator 가 다음
    leaf 와의 dedup existing set 에 추가).
    """
    settings = get_settings()
    inserted_count = 0
    new_keys: list[dedup_module.DedupKey] = []

    # === 그룹 1: 신규 INSERT 시도 (충돌 시 기존 lookup 으로 fallback) ===
    for r in to_insert:
        doc_id, is_new = await _insert_document_idempotent(db, sentinel_source_id, r)
        if doc_id is None:
            # Document INSERT 실패 (canonical_url/doi 둘 다 없거나 race 후 lookup 실패) — skip
            continue
        await _upsert_document_topic(db, doc_id, leaf, r.confidence)
        new_keys.append(dedup_module.make_key(r, document_id=doc_id))
        if is_new:
            inserted_count += 1
        if settings.CLICKBAIT_ENABLED:
            # TODO A5: clickbait_client.classify(doc) → INSERT ClickbaitResult
            pass

    # === 그룹 2: 매핑-only (이미 알려진 기존 Document) ===
    for r, existing_doc_id in to_link:
        await _upsert_document_topic(db, existing_doc_id, leaf, r.confidence)
        # 통계상 inserted 카운트 X (Document 신규 INSERT 아님). new_keys 도 추가 X
        # (이미 existing 안에 있음).

    return inserted_count, new_keys


async def _insert_document_idempotent(
    db: AsyncSession, sentinel_source_id: UUID, r: SearchResult
) -> tuple[UUID | None, bool]:
    """Document INSERT — cross-user URL/DOI 충돌 흡수.

    (round 3 R2-C01/C02 fix):
    Postgres partial unique index 에 대해 `on_conflict_do_nothing(index_elements=[...])`
    가 `WHERE canonical_url IS NOT NULL` 조건을 매칭하지 않아 infer 실패 가능 +
    canonical_url/doi 둘 다 있을 때 둘 다 잡으려면 untargeted `on_conflict_do_nothing()`
    가 가장 안전. (모든 unique constraint 에 대해 DO NOTHING.)

    (round 3 R2-S06 fix) `(document_id, is_new: bool)` 튜플 반환 — 신규 INSERT 통계 정확.

    플로우:
    1. canonical_url 또는 doi 가 이미 존재하면 pre-lookup 으로 기존 id 반환 (race 회피)
    2. 그 외 untargeted on_conflict_do_nothing INSERT RETURNING
    3. 충돌 시 (concurrent INSERT race) canonical → doi 순 fallback lookup
    """
    # 1. pre-lookup — race window 축소 + ON CONFLICT 의 partial index infer 의존 회피
    existing_id = await _lookup_existing_document_id(db, r)
    if existing_id is not None:
        return existing_id, False

    # 2. untargeted on_conflict_do_nothing INSERT — 모든 unique constraint 안전
    values: dict[str, Any] = {
        "document_id": uuid4(),
        "source_id": sentinel_source_id,
        "title": r.title,
        "normalized_title": dedup_module.normalize_title(r.title),
        "url": r.url,
        "canonical_url": r.canonical_url,
        "doi": r.doi,
        "summary": r.abstract_summary,
        "published_at": r.published_at,
        "content_type": _classify_content_type(r).value,
        "raw": {
            "publisher_domain": r.publisher_domain,
            "publisher_label": r.publisher_label,
            **r.raw,
        },
    }
    stmt = (
        pg_insert(Document)
        .values(**values)
        .on_conflict_do_nothing()
        .returning(Document.document_id)
    )
    result = await db.execute(stmt)
    inserted_id = result.scalar_one_or_none()
    if inserted_id is not None:
        return inserted_id, True

    # 3. race 발생 (pre-lookup 후 다른 worker 가 먼저 INSERT) → 재 lookup
    existing_id = await _lookup_existing_document_id(db, r)
    return existing_id, False


async def _lookup_existing_document_id(
    db: AsyncSession, r: SearchResult
) -> UUID | None:
    """canonical_url → doi 순 lookup. 둘 다 None 이면 None 반환."""
    if r.canonical_url:
        stmt = select(Document.document_id).where(
            Document.canonical_url == r.canonical_url
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
    if r.doi:
        stmt = select(Document.document_id).where(Document.doi == r.doi)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
    return None


async def _upsert_document_topic(
    db: AsyncSession, document_id: UUID, leaf: LeafTarget, confidence: float
) -> None:
    """DocumentTopic upsert (round 3 R2-S04 fix).

    DO UPDATE SET confidence = greatest(excluded, current) — 더 높은 confidence 유지
    (조용한 staleness 차단). partial UNIQUE 3종 모두 conflict target 으로 시도.
    """
    # cso_topic_id / leaf_topic_id 의 NULL 패턴에 따라 alembic 0003 의 3종 partial
    # UNIQUE INDEX 중 정확히 하나가 매칭. 그 index 의 conflict target 으로 DO UPDATE.
    if leaf.leaf_topic_id is None and leaf.parent_cso_topic_id is not None:
        index_where = sa_text(
            "leaf_topic_id IS NULL AND cso_topic_id IS NOT NULL"
        )
        elements: list[str] = ["document_id", "cso_topic_id"]
    elif leaf.parent_cso_topic_id is None and leaf.leaf_topic_id is not None:
        index_where = sa_text(
            "cso_topic_id IS NULL AND leaf_topic_id IS NOT NULL"
        )
        elements = ["document_id", "leaf_topic_id"]
    else:
        index_where = sa_text(
            "cso_topic_id IS NOT NULL AND leaf_topic_id IS NOT NULL"
        )
        elements = ["document_id", "cso_topic_id", "leaf_topic_id"]

    base = pg_insert(DocumentTopic).values(
        id=uuid4(),
        document_id=document_id,
        cso_topic_id=leaf.parent_cso_topic_id,
        leaf_topic_id=leaf.leaf_topic_id,
        confidence=confidence,
    )
    stmt = base.on_conflict_do_update(
        index_elements=elements,
        index_where=index_where,
        set_={
            "confidence": sa_func.greatest(
                base.excluded.confidence, DocumentTopic.confidence
            ),
        },
    )
    await db.execute(stmt)


# (Codex S-09) 도메인·trust_hint 기반 분류. default = VENDOR_BLOG.
_ACADEMIC_DOMAINS = frozenset(
    {"arxiv.org", "openalex.org", "doi.org", "semanticscholar.org", "dblp.org"}
)
_NEWS_DOMAINS = frozenset(
    {"news.naver.com", "techcrunch.com", "theverge.com", "wired.com", "arstechnica.com"}
)


def _classify_content_type(r: SearchResult) -> ContentType:
    domain = (r.publisher_domain or "").lower()
    if any(d in domain for d in _ACADEMIC_DOMAINS):
        return ContentType.ACADEMIC_PAPER
    if any(d in domain for d in _NEWS_DOMAINS):
        return ContentType.TECH_NEWS
    hint = str(r.raw.get("trust_hint", "")).lower()
    if hint == "academic":
        return ContentType.ACADEMIC_PAPER
    if hint == "news":
        return ContentType.TECH_NEWS
    return ContentType.VENDOR_BLOG


async def _finalize_job(
    db: AsyncSession,
    job_id: UUID,
    *,
    status: CollectionJobStatus,
    failure_reason: str | None,
) -> None:
    """CollectionJob row UPDATE — finished_at / status / failure_reason."""
    stmt = select(CollectionJob).where(CollectionJob.job_id == job_id)
    job = (await db.execute(stmt)).scalar_one()
    job.status = status.value
    job.failure_reason = failure_reason
    job.finished_at = datetime.now(UTC)
    await db.commit()


__all__ = [
    "LLM_SEARCH_SENTINEL_NAME",
    "CollectionAlreadyRunning",
    "CollectionJobResult",
    "LeafTarget",
    "build_trace_json",
    "deterministic_jitter_seconds",
    "load_existing_dedup_keys",
    "resolve_active_leaves",
    "run_collection_for_user",
]
