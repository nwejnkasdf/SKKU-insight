"""Trace operation 5종 — extend / retract / split / archive / merge.

각 함수는 user-mutex 보유 가정 (caller 가 `RedisKey.traversal_lock(user_id)` 잠금).
atomic SQL mutation 사용 (A6 C-01 anti-pattern 회피 — read-then-write 금지).

cso-topic-traversal.md §3 + A7 결정 매트릭스 (decisions.md §12):
- #17 merge operation 신규 도입
- #20 split 후 T 단축 + T'=분기점+B
- #22 merge 후 winner path 유지 + loser leaf 통합 + merged_into_trace_id
- merge 룰 trigger 는 merge_evaluator.py 에서, 본 모듈은 execute 만 담당
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, select, text, update
from sqlalchemy import func as sa_func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import LeafTopicStatus, TraversalStatus
from app.db.models import (
    DynamicLeafTopic,
    DynamicLeafTopicCSOTopic,
    UserCSOTraversal,
)
from app.traversal.protocol import MergePlan, RetractPlan, SplitPlan

logger = logging.getLogger(__name__)


# ============================================================
# extend — path.append (atomic SQL array_append)
# ============================================================


async def execute_extend(
    db: AsyncSession,
    trace_id: UUID,
    new_cso_topic_id: UUID,
    active_day_counter: int,
) -> bool:
    """trace.path.append(new_cso_topic_id). atomic array_append.

    A6 C-01 fix 패턴: read-then-write 회피. 단일 SQL UPDATE 로 array_append +
    cardinality cap (depth_cap=8) 확인 + last_activity_active_day 갱신.

    return True 시 path 변경됨. False 시 cap 초과 (path 이미 8) 또는 trace 없음.
    """
    settings = get_settings()
    # atomic SQL: UPDATE WHERE cardinality(path) < cap AND status='active'.
    # cardinality(path) cap 검사를 SQL 안에 박아 race 차단.
    stmt = (
        update(UserCSOTraversal)
        .where(
            UserCSOTraversal.trace_id == trace_id,
            UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
            sa_func.cardinality(UserCSOTraversal.path) < settings.TRACE_PATH_DEPTH_CAP,
        )
        .values(
            path=sa_func.array_append(UserCSOTraversal.path, new_cso_topic_id),
            last_activity_active_day=active_day_counter,
            updated_at=datetime.now(UTC),
        )
        .returning(UserCSOTraversal.trace_id)
    )
    result = await db.execute(stmt)
    extended = result.scalar_one_or_none()
    if extended is None:
        logger.info(
            "extend skipped: trace=%s reason=cap_or_not_active", trace_id
        )
        return False
    # (C-66, 2026-05-26) path 변경 후 score_tail sync — 새 tail (new_cso_topic_id) 의
    # UserInterestState.long_score 로 갱신. 옛 tail 값 잔존 방지. 다음 ingest 까지
    # 대기 (자연 sync) 보다 즉시 sync — 같은 트랜잭션 안 1 추가 SQL, 가독성 우선.
    await sync_score_tail_for_trace(db, trace_id)
    return True


# ============================================================
# retract — path.pop + LLM leaf 재배치
# ============================================================


async def execute_retract(
    db: AsyncSession,
    plan: RetractPlan,
    active_day_counter: int,
    leaf_remap_decisions: list[dict[str, Any]],
) -> int:
    """retract 실행. `leaf_remap_decisions` 는 LLM 응답을 caller 가 파싱한 결과.

    `leaf_remap_decisions`: [{"leaf_id": UUID, "decision": "remap"|"archive",
        "new_cso_topic_id": UUID | None}].

    1. trace.path 의 retracted_cso_topic_id 제거 (array_remove). last_activity 갱신.
       (Codex R1 Critical 1) status == stale 필터 + cardinality > 1 (path 비우기 차단) +
       returning rowcount 확인. UPDATE rowcount=0 시 leaf 변경 skip — trace mutation
       성공 확인 후에만 leaf 변경 (false positive retract 차단).
    2. leaf_remap_decisions 적용:
       - "remap": DynamicLeafTopicCSOTopic 갱신 (기존 cso=retracted → new).
       - "archive": DynamicLeafTopic.status='archived'.

    return: 재매핑된 leaf 수 (path UPDATE 실패 시 0).
    """
    # 1. atomic path.pop (array_remove). path=[a,b,c] retract c → [a,b].
    # (Codex R1 Critical 1) status=stale 필터 + path 길이 검증 + RETURNING rowcount 확인.
    path_stmt = (
        update(UserCSOTraversal)
        .where(
            UserCSOTraversal.trace_id == plan.trace_id,
            UserCSOTraversal.status == TraversalStatus.STALE.value,
            sa_func.cardinality(UserCSOTraversal.path) > 1,
        )
        .values(
            path=sa_func.array_remove(
                UserCSOTraversal.path, plan.retracted_cso_topic_id
            ),
            last_activity_active_day=active_day_counter,
            updated_at=datetime.now(UTC),
        )
        .returning(UserCSOTraversal.trace_id)
    )
    updated = (await db.execute(path_stmt)).scalar_one_or_none()
    if updated is None:
        # trace 가 stale 가 아니거나 path 길이 1 → retract 부적합. leaf 변경 skip.
        logger.info(
            "retract skipped: trace=%s reason=not_stale_or_single_node",
            plan.trace_id,
        )
        return 0

    # (C-66, 2026-05-26) path 변경 후 score_tail sync — 새 tail (parent 노드) 의
    # long_score 로 갱신. retracted cso 값 잔존 방지.
    await sync_score_tail_for_trace(db, plan.trace_id)

    # 2. leaf decisions 적용.
    remapped = 0
    for decision in leaf_remap_decisions:
        leaf_id = decision["leaf_id"]
        action = decision.get("decision", "archive")
        if action == "archive":
            await db.execute(
                update(DynamicLeafTopic)
                .where(DynamicLeafTopic.leaf_topic_id == leaf_id)
                .values(status=LeafTopicStatus.ARCHIVED.value)
            )
        elif action == "remap":
            new_cso = decision.get("new_cso_topic_id")
            if new_cso is None:
                continue
            # (Codex R2-RG-4 fix) DynamicLeafTopicCSOTopic composite PK 충돌 회피.
            # leaf 가 이미 new_cso 에 바인딩됐으면 UPDATE 가 PK 위반 — 대신 retracted
            # row 만 DELETE (new 매핑이 이미 존재, leaf 는 new 산하 유지).
            exists_stmt = select(
                DynamicLeafTopicCSOTopic.leaf_topic_id
            ).where(
                DynamicLeafTopicCSOTopic.leaf_topic_id == leaf_id,
                DynamicLeafTopicCSOTopic.cso_topic_id == new_cso,
            )
            already_mapped = (
                await db.execute(exists_stmt)
            ).scalar_one_or_none()
            if already_mapped is not None:
                # 기존 retracted 매핑만 삭제 (new 매핑은 보존).
                await db.execute(
                    delete(DynamicLeafTopicCSOTopic).where(
                        DynamicLeafTopicCSOTopic.leaf_topic_id == leaf_id,
                        DynamicLeafTopicCSOTopic.cso_topic_id
                        == plan.retracted_cso_topic_id,
                    )
                )
                remapped += 1
                continue
            # DynamicLeafTopicCSOTopic 의 retracted cso_topic_id 행 → new cso 로 갱신.
            await db.execute(
                update(DynamicLeafTopicCSOTopic)
                .where(
                    DynamicLeafTopicCSOTopic.leaf_topic_id == leaf_id,
                    DynamicLeafTopicCSOTopic.cso_topic_id
                    == plan.retracted_cso_topic_id,
                )
                .values(cso_topic_id=new_cso)
            )
            remapped += 1
    return remapped


# ============================================================
# split — T 단축 + T'=분기점+B (A7 결정 #20)
# ============================================================


async def execute_split(
    db: AsyncSession,
    plan: SplitPlan,
    user_id: UUID,
    active_day_counter: int,
    leaf_dispatch_decisions: list[dict[str, Any]],
) -> UUID:
    """split 실행 — T 단축 + T'=분기점+B 신규 생성.

    A7 결정 #20 (cso-topic-traversal.md §3.3 docs 갱신 대상):
    - source trace T: path = truncated_path (분기점까지 단축).
    - new trace T': path = new_path (분기점 + child_B), status='active'.
    - leaf_dispatch_decisions: [{"leaf_id": UUID, "target_trace": "source"|"new",
        "target_cso_topic_id": UUID | None}].

    return: new_trace_id (T').
    """
    # 1. source trace T 단축.
    await db.execute(
        update(UserCSOTraversal)
        .where(UserCSOTraversal.trace_id == plan.source_trace_id)
        .values(
            path=plan.truncated_path,
            last_activity_active_day=active_day_counter,
            updated_at=datetime.now(UTC),
        )
    )
    # (C-66, 2026-05-26) source path 변경 후 score_tail sync — 새 tail (child_A) 의 long_score.
    await sync_score_tail_for_trace(db, plan.source_trace_id)

    # 2. 새 trace T' 생성. A6 C-03 패턴: pg_insert + returning + on_conflict_do_nothing.
    new_trace_id = uuid.uuid4()
    now = datetime.now(UTC)
    insert_stmt = (
        pg_insert(UserCSOTraversal)
        .values(
            trace_id=new_trace_id,
            user_id=user_id,
            path=plan.new_path,
            status=TraversalStatus.ACTIVE.value,
            started_active_day=active_day_counter,
            last_activity_active_day=active_day_counter,
            score_tail=0.0,
            merged_into_trace_id=None,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["trace_id"])
        .returning(UserCSOTraversal.trace_id)
    )
    result = await db.execute(insert_stmt)
    inserted = result.scalar_one_or_none()
    if inserted is None:
        # race — uuid4 collision 극히 희박. 무시.
        logger.warning(
            "split new trace insert race: source=%s", plan.source_trace_id
        )
    else:
        # (C-66) 새 T' INSERT 후 score_tail sync — path tail (child_B) 의 long_score.
        await sync_score_tail_for_trace(db, inserted)

    # 3. leaf dispatch 적용 (LLM 결정에 따라 양 trace 에 분배).
    for decision in leaf_dispatch_decisions:
        leaf_id = decision["leaf_id"]
        target = decision.get("target_trace", "source")
        target_cso = decision.get("target_cso_topic_id")
        if target_cso is None:
            continue
        # 기존 매핑 (분기점 산하) 의 cso_topic_id 를 target 으로 갱신.
        # source 면 cso 유지 가능, new 면 child_B 로 변경.
        await db.execute(
            update(DynamicLeafTopicCSOTopic)
            .where(
                DynamicLeafTopicCSOTopic.leaf_topic_id == leaf_id,
                DynamicLeafTopicCSOTopic.cso_topic_id == plan.fork_cso_topic_id,
            )
            .values(cso_topic_id=target_cso)
        )
    _ = target  # for type checker
    return new_trace_id


# ============================================================
# archive — status='archived' + 산하 leaf 동반 archive
# ============================================================


async def execute_archive(
    db: AsyncSession,
    trace_id: UUID,
    user_id: UUID,
    active_day_counter: int,
) -> int:
    """trace.status='archived' + path 산하 active leaf 도 archive.

    no LLM 호출. cso-topic-traversal.md §3.4 (3단계 강등 마지막 단계).
    return: 동반 archive 된 leaf 수.

    (Codex R3-NEW-S2 fix) 1 atomic UPDATE ... RETURNING path 로 SELECT+UPDATE race 차단.
    이전 구현은 SELECT 후 UPDATE 사이에 다른 worker (lock 없이) 가 status 변경하면
    stale path 기준 archive 가능 — caller 가 traversal_lock 보유해도 SQL transaction
    boundary 안 다른 row 영향. RETURNING path 가 archive 직전 SQL snapshot 보장.

    (C-44 P2-27 fix, 2026-05-19) active_day_counter 인자 추가 — archive 시점의
    user.active_day_counter 값을 archived_at_active_day 컬럼에 저장. A8-v2
    reincarnation 의 gap_days_min 가드가 last_activity 가 아닌 본 컬럼 기준 비교.

    (C-65 D1 fix, 2026-05-26) status='archived' UPDATE 직전 score_tail freeze —
    path 끝 long_score 값을 score_tail 컬럼에 영구 저장. A8-v2 Reincarnation 후보 풀
    (`get_archived_traces_with_score(score_tail_min=0.6)`) 의 source. freeze 시점 이후는
    archived row 라 sync 호출 없음 (status='archived' 는 sync_score_tail_for_trace 의
    JOIN WHERE 조건이 아님 — 즉 본 freeze 가 마지막 갱신 기회).
    """
    # (C-65) archive 직전 score_tail freeze — single trace, surgical.
    await sync_score_tail_for_trace(db, trace_id)

    # 1. atomic UPDATE — status=archived AND status != archived (idempotent), RETURNING path.
    update_stmt = (
        update(UserCSOTraversal)
        .where(
            UserCSOTraversal.trace_id == trace_id,
            UserCSOTraversal.status != TraversalStatus.ARCHIVED.value,
        )
        .values(
            status=TraversalStatus.ARCHIVED.value,
            archived_at_active_day=active_day_counter,
            updated_at=datetime.now(UTC),
        )
        .returning(UserCSOTraversal.path)
    )
    result = await db.execute(update_stmt)
    path_row = result.scalar_one_or_none()
    if path_row is None:
        # 이미 archived 또는 trace 부재.
        return 0
    path_ids = list(path_row)

    # 2. 산하 active+emerging leaf 도 archive.
    if not path_ids:
        return 0
    leaf_stmt = (
        update(DynamicLeafTopic)
        .where(
            DynamicLeafTopic.user_id == user_id,
            DynamicLeafTopic.status.in_(
                [LeafTopicStatus.ACTIVE.value, LeafTopicStatus.EMERGING.value]
            ),
            DynamicLeafTopic.leaf_topic_id.in_(
                select(DynamicLeafTopicCSOTopic.leaf_topic_id).where(
                    DynamicLeafTopicCSOTopic.cso_topic_id.in_(path_ids)
                )
            ),
        )
        .values(status=LeafTopicStatus.ARCHIVED.value)
        .returning(DynamicLeafTopic.leaf_topic_id)
    )
    result = await db.execute(leaf_stmt)
    archived = list(result.scalars().all())
    return len(archived)


# ============================================================
# merge — winner 유지 + loser archived + merged_into_trace_id (A7 신규)
# ============================================================


async def execute_merge(
    db: AsyncSession,
    plan: MergePlan,
    user_id: UUID,
    active_day_counter: int,
) -> int:
    """trace merge 실행 (A7 결정 #22).

    1. loser trace.status='archived' + merged_into_trace_id = winner_id.
    2. winner trace.last_activity_active_day 갱신 (활동도 합산 의미).
    3. loser 산하 leaf 의 cso_topic 매핑이 winner.path 와 겹치지 않으면
       winner.path 의 가장 가까운 cso_topic 으로 재매핑 (간단화: winner.path 끝 노드).
       이미 winner.path 위 노드에 매핑된 leaf 는 그대로 둠 (중복 매핑 가능).

    return: 재매핑된 leaf 수.
    """
    # 1. loser archive + merged_into 마킹.
    # (C-44 P2-27 fix, 2026-05-19) archived_at_active_day = active_day_counter.
    # (C-65 D1 fix, 2026-05-26) loser archive 직전 score_tail freeze — execute_archive
    # 와 동일 패턴. Reincarnation 후보 풀 (`score_tail >= 0.6`) 의 source 인데 merged
    # loser 는 `merged_into_trace_id IS NULL` 가드로 풀에서 자동 제외이지만, audit/recovery
    # 의미상 freeze 값 보존.
    await sync_score_tail_for_trace(db, plan.loser_trace_id)
    await db.execute(
        update(UserCSOTraversal)
        .where(
            and_(
                UserCSOTraversal.trace_id == plan.loser_trace_id,
                UserCSOTraversal.user_id == user_id,
            )
        )
        .values(
            status=TraversalStatus.ARCHIVED.value,
            merged_into_trace_id=plan.winner_trace_id,
            archived_at_active_day=active_day_counter,
            updated_at=datetime.now(UTC),
        )
    )

    # 2. winner last_activity 갱신.
    await db.execute(
        update(UserCSOTraversal)
        .where(UserCSOTraversal.trace_id == plan.winner_trace_id)
        .values(
            last_activity_active_day=active_day_counter,
            updated_at=datetime.now(UTC),
        )
    )

    # 3. leaf 매핑 갱신.
    winner_row = (
        await db.execute(
            select(UserCSOTraversal).where(
                UserCSOTraversal.trace_id == plan.winner_trace_id
            )
        )
    ).scalar_one_or_none()
    if winner_row is None or not winner_row.path:
        return 0
    winner_path_set = set(winner_row.path)
    winner_tail = winner_row.path[-1]

    reassigned = 0
    for leaf_id in plan.leaves_to_reassign:
        # leaf 의 cso_topic 매핑 list 조회.
        mappings = (
            await db.execute(
                select(DynamicLeafTopicCSOTopic).where(
                    DynamicLeafTopicCSOTopic.leaf_topic_id == leaf_id
                )
            )
        ).scalars().all()
        # 이미 winner.path 위 노드에 매핑된 leaf 는 skip.
        if any(m.cso_topic_id in winner_path_set for m in mappings):
            continue
        # 매핑 없는 경우 (이상 케이스) skip.
        if not mappings:
            continue
        # 첫 매핑의 cso_topic_id 를 winner.path 의 끝 (winner_tail) 로 재매핑.
        first = mappings[0]
        await db.execute(
            update(DynamicLeafTopicCSOTopic)
            .where(
                and_(
                    DynamicLeafTopicCSOTopic.leaf_topic_id == leaf_id,
                    DynamicLeafTopicCSOTopic.cso_topic_id == first.cso_topic_id,
                )
            )
            .values(cso_topic_id=winner_tail)
        )
        reassigned += 1
    return reassigned


# ============================================================
# stale 마킹 (1단계 강등) — ingest 직후 즉시 평가 (no LLM, no cron 의존)
# ============================================================


async def mark_stale_if_idle(
    db: AsyncSession,
    user_id: UUID,
    active_day_counter: int,
) -> int:
    """idle ≥ TRACE_STALE_IDLE_DAYS AND score_tail ≤ TRACE_STALE_THRESHOLD_SCORE 인
    active trace 를 stale 로 즉시 전이 (A7 결정 #7 하이브리드 — 1단계 즉시).

    no LLM. atomic SQL UPDATE 1회. return: 마킹된 trace 수.

    (C-65, 2026-05-26) caller (interest/service.py:ingest_event_atomic) 가 본 호출 직전에
    `sync_score_tail_for_user` 를 실행해 path 끝 노드 long_score → score_tail 컬럼을
    최신화한 상태에서 본 SQL 의 score_tail 조건 평가가 의미를 가짐. D1 fix.
    """
    settings = get_settings()
    stale_threshold = settings.TRACE_STALE_THRESHOLD_SCORE
    stale_idle = settings.TRACE_STALE_IDLE_DAYS
    stmt = (
        update(UserCSOTraversal)
        .where(
            UserCSOTraversal.user_id == user_id,
            UserCSOTraversal.status == TraversalStatus.ACTIVE.value,
            (active_day_counter - UserCSOTraversal.last_activity_active_day)
            >= stale_idle,
            UserCSOTraversal.score_tail <= stale_threshold,
        )
        .values(
            status=TraversalStatus.STALE.value,
            updated_at=datetime.now(UTC),
        )
        .returning(UserCSOTraversal.trace_id)
    )
    result = await db.execute(stmt)
    marked = list(result.scalars().all())
    return len(marked)


# ============================================================
# score_tail sync (C-65, 2026-05-26) — D1 fix
# ============================================================
#
# cso-topic-traversal.md §1 schema 명세: "score_tail: float — path 끝 노드의 베이지안
# 사후 평균 (캐시)". 직전까지 모든 INSERT 가 0.0 default 였고 갱신 caller 부재 →
# Reincarnation discovery slot 완전 무력화 (`score_tail >= 0.6` 후보 항상 0건) +
# core_softmax 균등 분포 (의도된 강한 trace 가중치 무효).
#
# 갱신 책임 위치 (옵션 C+D 결합):
# - 옵션 D: `interest/service.py:ingest_event_atomic` step 7.5 (베이지안 사후 갱신 직후,
#   traversal_lock 보유 시만 — race-safe). 사용자 전체 trace 의 path tail sync.
# - 옵션 C: `archive_if_eligible` / `execute_archive` 진입 직전 (archive 시점 freeze) —
#   reincarnation 후보 풀의 source. 단일 trace sync.


async def sync_score_tail_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> int:
    """사용자의 모든 active/stale trace 의 path 끝 cso 의 `UserInterestState.long_score`
    를 `score_tail` 컬럼에 sync. 단일 atomic SQL.

    JOIN 조건: `UserInterestState.cso_topic_id == path[cardinality(path)]` AND
    `leaf_topic_id IS NULL` (cso-only partial UNIQUE `ux_user_interest_state_cso_only`
    인덱스 사용). row 없는 cso 는 score_tail=0.0 (LEFT JOIN + COALESCE).

    IS DISTINCT FROM 가드 — 값 변동 없으면 UPDATE skip (no-op 트랜잭션 부담 회피).

    Returns: 실제 값이 변동된 trace 수.
    """
    stmt = text(
        """
        UPDATE user_cso_traversal AS t
        SET score_tail = sub.tail_score,
            updated_at = NOW()
        FROM (
            SELECT t2.trace_id,
                   COALESCE(uis.long_score, 0.0) AS tail_score
            FROM user_cso_traversal AS t2
            LEFT JOIN user_interest_state AS uis
              ON uis.user_id = t2.user_id
              AND uis.cso_topic_id = t2.path[cardinality(t2.path)]
              AND uis.leaf_topic_id IS NULL
            WHERE t2.user_id = :user_id
              AND t2.status IN ('active', 'stale')
        ) AS sub
        WHERE t.trace_id = sub.trace_id
          AND t.score_tail IS DISTINCT FROM sub.tail_score
        """
    )
    result = await db.execute(stmt, {"user_id": user_id})
    return int(result.rowcount or 0)


async def sync_score_tail_for_trace(
    db: AsyncSession,
    trace_id: UUID,
) -> bool:
    """단일 trace 의 path 끝 cso long_score → score_tail sync.

    execute_archive / execute_retract / execute_extend 처럼 path 변경 직후 그 trace 만
    sync 할 때 사용. user 전체 sync 보다 surgical.

    Returns: 값 변동 발생 시 True.
    """
    stmt = text(
        """
        UPDATE user_cso_traversal AS t
        SET score_tail = COALESCE(sub.long_score, 0.0),
            updated_at = NOW()
        FROM (
            SELECT t2.trace_id, uis.long_score
            FROM user_cso_traversal AS t2
            LEFT JOIN user_interest_state AS uis
              ON uis.user_id = t2.user_id
              AND uis.cso_topic_id = t2.path[cardinality(t2.path)]
              AND uis.leaf_topic_id IS NULL
            WHERE t2.trace_id = :trace_id
        ) AS sub
        WHERE t.trace_id = sub.trace_id
          AND t.score_tail IS DISTINCT FROM COALESCE(sub.long_score, 0.0)
        """
    )
    result = await db.execute(stmt, {"trace_id": trace_id})
    return (result.rowcount or 0) > 0


__all__ = [
    "execute_archive",
    "execute_extend",
    "execute_merge",
    "execute_retract",
    "execute_split",
    "mark_stale_if_idle",
    "sync_score_tail_for_trace",
    "sync_score_tail_for_user",
]
