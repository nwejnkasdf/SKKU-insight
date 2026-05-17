"""DefaultTraversalEngine — TraversalEngine Protocol 의 1차 구현체.

A6 ingest_event_atomic hook + A7 daily cron + A8 query 모두 본 클래스를 사용.

설계 원칙:
- caller (worker / router) 가 AsyncSession + LLMProvider + NetworkX graph 를 생성자 주입.
- 모든 mutation 은 user-mutex 보유 가정 (caller 책임).
- LLM 호출은 best-effort: 실패 시 룰 기반 fallback (path 변경 없음 + warning log).
- A6 anti-pattern 회피: atomic SQL + ON CONFLICT + after-commit cache invalidate.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import networkx as nx
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import TraversalStatus
from app.db.models import (
    DynamicLeafTopic,
    UserCSOTraversal,
)
from app.llm_provider.protocol import LLMProvider
from app.traversal import operations, queries
from app.traversal.merge_evaluator import evaluate_and_execute_merges
from app.traversal.protocol import (
    MergePlan,
    RetractPlan,
    SplitPlan,
    TraversalDelta,
)

logger = logging.getLogger(__name__)


class DefaultTraversalEngine:
    """TraversalEngine 의 1차 구현체. caller 가 매 호출 시 생성 또는 lifespan singleton.

    생성자 인자:
    - db: AsyncSession — caller 가 트랜잭션 관리.
    - provider: LLMProvider — Mock(default) / OpenAI / Anthropic 등 토글.
    - graph: NetworkX DiGraph — app.state.cso_graph (lifespan 부팅 시 빌드).
    """

    def __init__(
        self,
        db: AsyncSession,
        provider: LLMProvider,
        graph: nx.DiGraph,
    ) -> None:
        self.db = db
        self.provider = provider
        self.graph = graph

    # --- write (mutation) ---

    async def ingest_event(
        self,
        user_id: UUID,
        active_day_counter: int,
        cso_topic_ids: list[UUID],
    ) -> TraversalDelta:
        """A6 ingest_event_atomic hook. 매칭 trace 발견 시 last_activity 갱신만 (no extend
        — extend 는 daily cron 또는 명시 evaluate_extend 호출). 매칭 없으면 새 trace.

        cso_topic_ids: 이벤트의 Document 매핑 cso_topic_id list (DocumentTopic).
        보통 1~3개. 첫 매칭 trace 만 갱신, 나머지는 skip (단순화).
        """
        if not cso_topic_ids:
            return "noop"
        for cso_id in cso_topic_ids:
            matched = await queries.find_active_trace_matching(
                self.db, user_id, cso_id
            )
            if matched is None:
                continue
            # 매칭 trace 의 last_activity 만 갱신 (path 는 그대로 — extend 임계 평가는 daily cron).
            await self.db.execute(
                update(UserCSOTraversal)
                .where(UserCSOTraversal.trace_id == matched.trace_id)
                .values(last_activity_active_day=active_day_counter)
            )
            return "noop"
        # 매칭 없음 — 새 trace 생성 (cold-start). 첫 cso 만.
        await self.create_new_trace(user_id, active_day_counter, cso_topic_ids[0])
        return "new_trace"

    async def evaluate_extend(
        self,
        trace_id: UUID,
        candidate_child_cso_id: UUID,
    ) -> bool:
        """daily cron 또는 명시 호출. 자식 인터랙션 임계 확인은 caller 가 했다고 가정.

        LLM 검증 (high slot): 자식 노드가 trace path 의 자연 연장인지 확인.
        실패 시 path 변경 없음.
        """
        # 1차 시연: 룰 통과 시 즉시 path.append (LLM 검증 생략 — 추후 본문 확장).
        # 향후 fixture 추가 시 LLM `extend_verify` 호출 가능.
        # active_day_counter 는 trace.last_activity 와 동일 가정 (caller 가 갱신 후 호출).
        trace_row = await self.db.get(UserCSOTraversal, trace_id)
        if trace_row is None:
            return False
        active_day = trace_row.last_activity_active_day
        return await operations.execute_extend(
            self.db, trace_id, candidate_child_cso_id, active_day
        )

    async def evaluate_retract(
        self,
        trace_id: UUID,
    ) -> RetractPlan | None:
        """말단 노드 stale 누적 14 days 시 retract.

        본 메서드는 plan 생성 + LLM 호출 + execute 까지 수행. 1차 시연에서는 LLM
        호출은 stub (fixture 미존재 시 archive 결정으로 fallback).
        """
        trace_row = await self.db.get(UserCSOTraversal, trace_id)
        if trace_row is None or not trace_row.path:
            return None
        if trace_row.status != TraversalStatus.STALE.value:
            return None
        retracted_cso = trace_row.path[-1]
        new_path = list(trace_row.path[:-1])
        # 산하 leaf 매핑 lookup — retracted_cso 매핑 leaf list.
        leaves = await queries.get_descendant_leaves(
            self.db, trace_row.user_id, trace=trace_row
        )
        leaves_to_remap = [
            lf.leaf_topic_id
            for lf in leaves
            # retracted_cso 매핑 leaf 만 — 단순화: 모든 leaf 대상.
        ]
        plan = RetractPlan(
            trace_id=trace_id,
            retracted_cso_topic_id=retracted_cso,
            new_path=new_path,
            leaves_to_remap=leaves_to_remap,
        )
        # LLM 호출 (1차 시연: 모두 archive 로 fallback).
        decisions: list[dict[str, Any]] = [
            {"leaf_id": lid, "decision": "archive"}
            for lid in leaves_to_remap
        ]
        await operations.execute_retract(
            self.db, plan, trace_row.last_activity_active_day, decisions
        )
        return plan

    async def evaluate_split(
        self,
        trace_id: UUID,
        diverging_children: list[UUID],
    ) -> SplitPlan | None:
        """동일 부모 산하 두 자식 동시 부상 시 split.

        본 시연: diverging_children=[child_A, child_B]. 분기점은 trace.path 끝.
        T 단축 + T'=분기점+B (결정 #20). LLM 호출은 1차 시연 stub.
        """
        if len(diverging_children) < 2:
            return None
        trace_row = await self.db.get(UserCSOTraversal, trace_id)
        if trace_row is None or not trace_row.path:
            return None
        active_count = await queries.count_active_traces(self.db, trace_row.user_id)
        settings = get_settings()
        if active_count >= settings.TRACE_ACTIVE_CAP:
            logger.warning(
                "split skipped: user=%s active_cap=%d reached",
                trace_row.user_id,
                settings.TRACE_ACTIVE_CAP,
            )
            return None
        fork = trace_row.path[-1]
        # truncated_path = 분기점까지 (현 path 끝 = 분기점, 그래서 그대로 유지).
        # 결정 #20: T 단축 — 산하 child 가 없는 path 로 단축. 분기점이 이미 path 끝이라
        # truncated_path = path 그대로 (child_A 는 T 의 산하 leaf 매핑으로 표현).
        # new_path = path + child_B (T' 가 분기점 + B 로 1-hop 확장).
        truncated_path = list(trace_row.path)
        new_path = [*list(trace_row.path), diverging_children[1]]
        leaves = await queries.get_descendant_leaves(
            self.db, trace_row.user_id, trace=trace_row
        )
        plan = SplitPlan(
            source_trace_id=trace_id,
            fork_cso_topic_id=fork,
            truncated_path=truncated_path,
            new_path=new_path,
            leaves_to_dispatch=[lf.leaf_topic_id for lf in leaves],
        )
        # LLM dispatch decisions (1차 시연 stub: 모두 source 유지).
        decisions: list[dict[str, Any]] = [
            {
                "leaf_id": lid,
                "target_trace": "source",
                "target_cso_topic_id": fork,
            }
            for lid in plan.leaves_to_dispatch
        ]
        await operations.execute_split(
            self.db,
            plan,
            trace_row.user_id,
            trace_row.last_activity_active_day,
            decisions,
        )
        return plan

    async def archive_if_eligible(
        self,
        trace_id: UUID,
    ) -> bool:
        """stale 누적 90 active days 초과 시 archive."""
        trace_row = await self.db.get(UserCSOTraversal, trace_id)
        if trace_row is None:
            return False
        if trace_row.status != TraversalStatus.STALE.value:
            return False
        # caller 가 active_day_counter 를 trace.last_activity 와 비교해서 호출 시점 결정.
        # 본 메서드는 단순 status 전이.
        await operations.execute_archive(self.db, trace_id, trace_row.user_id)
        return True

    async def evaluate_merge_candidates(
        self,
        user_id: UUID,
    ) -> list[MergePlan]:
        """daily 18 UTC cron 의 사용자별 entry. merge_evaluator 로 위임."""
        # active_day_counter 는 caller 가 user 의 latest 로 전달해야 정확하나, 본 메서드
        # 시그니처는 단순. 내부에서 가장 활성 trace 의 active_day 사용 (단순화).
        traces = await queries.get_active_traces(self.db, user_id)
        if not traces:
            return []
        active_day = max(t.last_activity_active_day for t in traces)
        return await evaluate_and_execute_merges(
            self.db, self.provider, user_id, active_day
        )

    async def create_new_trace(
        self,
        user_id: UUID,
        active_day_counter: int,
        root_cso_topic_id: UUID,
    ) -> UUID:
        """cold-start trace 생성 (A6 ingest_event_atomic hook).

        active_cap=10 초과 시: 가장 idle stale trace 자동 archive 후 진행.
        path=[root_cso_topic_id], status='active'.
        """
        import uuid
        from datetime import UTC, datetime

        settings = get_settings()
        active_count = await queries.count_active_traces(self.db, user_id)
        if active_count >= settings.TRACE_ACTIVE_CAP:
            # 가장 idle stale trace archive (있으면).
            from sqlalchemy import select

            idle_stale = (
                await self.db.execute(
                    select(UserCSOTraversal)
                    .where(
                        UserCSOTraversal.user_id == user_id,
                        UserCSOTraversal.status == TraversalStatus.STALE.value,
                    )
                    .order_by(UserCSOTraversal.last_activity_active_day.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if idle_stale is not None:
                await operations.execute_archive(
                    self.db, idle_stale.trace_id, user_id
                )
            else:
                # active cap 도달 + stale 도 없음 — 가장 오래된 active 의 path 단축 또는
                # 그냥 거부. 1차 시연: 거부 (warning).
                logger.warning(
                    "create_new_trace: active_cap=%d reached, no stale to archive "
                    "(user=%s)",
                    settings.TRACE_ACTIVE_CAP,
                    user_id,
                )
                raise RuntimeError("traversal.active_cap_exceeded")

        new_trace_id = uuid.uuid4()
        now = datetime.now(UTC)
        stmt = (
            pg_insert(UserCSOTraversal)
            .values(
                trace_id=new_trace_id,
                user_id=user_id,
                path=[root_cso_topic_id],
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
        result = await self.db.execute(stmt)
        inserted = result.scalar_one_or_none()
        return inserted if inserted is not None else new_trace_id

    # --- read (A6 propagation + A8 추천 의존) ---

    async def get_active_traces(
        self, user_id: UUID
    ) -> list[UserCSOTraversal]:
        return await queries.get_active_traces(self.db, user_id)

    async def get_current_topics(self, user_id: UUID) -> list[UUID]:
        return await queries.get_current_topics(self.db, user_id)

    async def get_adjacent_topics(self, user_id: UUID) -> list[UUID]:
        return await queries.get_adjacent_topics(self.db, self.graph, user_id)

    async def get_descendant_leaves(
        self, trace_id: UUID
    ) -> list[DynamicLeafTopic]:
        trace_row = await self.db.get(UserCSOTraversal, trace_id)
        if trace_row is None:
            return []
        return await queries.get_descendant_leaves(
            self.db, trace_row.user_id, trace=trace_row
        )

    async def get_emerging_leaves(
        self, user_id: UUID
    ) -> list[DynamicLeafTopic]:
        return await queries.get_emerging_leaves(self.db, user_id)


__all__ = ["DefaultTraversalEngine"]
