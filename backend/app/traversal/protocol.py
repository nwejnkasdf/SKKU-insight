"""TraversalEngine Protocol + 보조 dataclass.

decisions.md §12 (A7 round, 2026-05-17) 결정 매트릭스 23건 반영. plan §12 결정 #12 잠정:
**옵션 A — write + read 메서드 모두 단일 Protocol 에 정의** (LLMProvider 패턴 동일).
A8 진입 시점 재확인하나, 1차 시연 단계는 단일 protocol 로 충분.

Operation 5 종 (cso-topic-traversal.md §3 + A7 결정 #17):
- extend: 자식 노드 인터랙션 ≥ 5건 → path.append + LLM 검증 1회
- retract: stale 누적 14 days → path.pop + LLM leaf 재배치 1회
- split: 두 자식 동시 부상 (7 days window) → T 단축 + T'=분기점+B + LLM leaf 분배 1회
- archive: stale 누적 90 days OR path 길이 0 → status='archived' (no LLM)
- merge (A7 신규): path overlap ≥3 → daily cron LLM 검증 → winner trace 유지

read 메서드 5 종 (A6 propagation + A8 추천 의존):
- get_active_traces(user_id) — 모든 active trace
- get_current_topics(user_id) — 모든 active trace 의 path 끝 노드 (core 후보)
- get_adjacent_topics(user_id) — path 끝 노드의 1-hop 그래프 이웃 (adjacent 후보)
- get_descendant_leaves(trace_id) — core leaf 후보 (active+emerging, merged/archived 제외)
- get_emerging_leaves(user_id) — emerging quota (core 5 중 1개) 후보
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from app.db.models import DynamicLeafTopic, UserCSOTraversal

# ============================================================
# Delta / Plan dataclasses
# ============================================================


@dataclass(slots=True, frozen=True)
class NoOp:
    """ingest_event 가 어떤 trace 도 mutate 안 함 (인터랙션이 path 위 노드 매칭 안 됨)."""

    reason: str  # "no_topic_match" | "consent_inactive" | ...


@dataclass(slots=True, frozen=True)
class RetractPlan:
    """retract operation 의 계획 (LLM leaf 재배치 입력)."""

    trace_id: UUID
    retracted_cso_topic_id: UUID  # 떼어낼 path 말단 노드
    new_path: list[UUID]          # path.pop 후의 새 path (단축됨)
    leaves_to_remap: list[UUID]   # retracted node 에 매핑된 leaf_topic_id 들
    # LLM 응답 후 결정: leaves_to_remap 각각이 (a) new_path 의 어느 노드로 재매핑 또는
    # (b) status='archived' 가 됨. operations.execute_retract 가 본 plan 을 받아 LLM 호출.


@dataclass(slots=True, frozen=True)
class SplitPlan:
    """split operation 의 계획 (T 단축 + T'=분기점+B, A7 결정 #20)."""

    source_trace_id: UUID
    fork_cso_topic_id: UUID       # 분기점 노드 (T 의 새 말단)
    truncated_path: list[UUID]    # T 의 새 path (분기점까지 단축)
    new_path: list[UUID]          # T' 의 path (분기점 + child_B)
    leaves_to_dispatch: list[UUID]  # 분기점 산하 leaf — LLM 이 양 trace 로 분배


@dataclass(slots=True, frozen=True)
class MergePlan:
    """trace merge operation 의 계획 (A7 신규, 결정 #17/#21/#22/#23).

    daily 18 UTC cron 이 path overlap ≥3 또는 proper subset 후보 발견 → LLM 검증.
    winner = max(last_activity_active_day), tie 시 trace_id 더 작은 쪽 (deterministic).
    """

    winner_trace_id: UUID
    loser_trace_id: UUID
    leaves_to_reassign: list[UUID]  # loser 산하 leaf — winner trace 로 재매핑


# extend 는 path.append 1 변경이라 별도 Plan 없이 bool 반환.
# archive 도 status='archived' 1 변경이라 bool 반환.

TraversalDelta = Literal[
    "noop",
    "extend",
    "retract",
    "split",
    "archive",
    "merge",
    "new_trace",  # cold-start trace 생성 (사용자 첫 카드 클릭 hook)
    # (C-62, 2026-05-25) boost trace 매칭 — origin onboarding_boost → behavioral 로 promote.
    # caller 는 본 신호로 다른 boost trace 정리.
    "promoted",
]


# ============================================================
# Protocol
# ============================================================


class TraversalEngine(Protocol):
    """TraversalEngine — A7 의 핵심 entry. trace 운영 + A8 의존 read API.

    구현체: `DefaultTraversalEngine` (app/traversal/default.py).
    """

    # --- write (mutation) ---

    async def ingest_event(
        self,
        user_id: UUID,
        active_day_counter: int,
        cso_topic_ids: list[UUID],
    ) -> TraversalDelta:
        """이벤트 1건의 cso_topic 매핑들을 받아 매칭 trace 업데이트 또는 새 trace 생성.

        A6 service.ingest_event_atomic 의 hook 으로 호출됨. 매칭되는 active trace 가
        없으면 새 trace 생성 (cold-start, 결정 #6). user-level mutex 보유 상태에서
        호출 (caller 가 traversal_lock 잠그고 호출).

        return:
        - "new_trace": 새 trace 생성 (path = [cso_topic_id])
        - "extend": 자식 매칭 — path.append (룰 임계 통과 + LLM 검증 후)
        - "noop": 매칭 trace 발견 — 인터랙션만 누적, 임계 미달
        """
        ...

    async def evaluate_extend(
        self,
        trace_id: UUID,
        candidate_child_cso_id: UUID,
    ) -> bool:
        """자식 노드 인터랙션 임계 (>= 5건) 통과 후 LLM 검증으로 extend 결정.

        path.append 까지 수행. False 반환 시 path 변경 없음 (LLM 거부).
        """
        ...

    async def evaluate_retract(
        self,
        trace_id: UUID,
    ) -> RetractPlan | None:
        """말단 노드 score_tail <= 0.30 AND stale 누적 14 days 시 retract 계획 + LLM 호출.

        execute 까지 수행 (path.pop + leaves_to_remap LLM 재매핑). None 반환 시 무변화.
        """
        ...

    async def evaluate_split(
        self,
        trace_id: UUID,
        diverging_children: list[UUID],
    ) -> SplitPlan | None:
        """동일 부모 산하 두 자식 동시 extend 임계 도달 (7 days window) 시 split 계획.

        T 단축 + T'=분기점+B path 처리 (A7 결정 #20). execute 까지 수행.
        active_cap=10 초과 시 None + LEAF_TRAVERSAL_ACTIVE_CAP_EXCEEDED warning.
        """
        ...

    async def archive_if_eligible(
        self,
        trace_id: UUID,
    ) -> bool:
        """stale 누적 90 active days 초과 OR path 길이 0 시 status='archived'.

        no LLM. 산하 active leaf 도 동반 archive (status='archived').
        """
        ...

    async def evaluate_merge_candidates(
        self,
        user_id: UUID,
    ) -> list[MergePlan]:
        """(A7 신규, 결정 #17/#21/#23) Daily 18 UTC cron 에서 룰 trigger 후 LLM 검증.

        룰: 두 active trace path 가 같은 cso_topic_id ≥3 공유 OR proper subset.
        LLM: trace_merge_verify 호출 (양 trace 의 leaf list + 라벨 + 활동도 입력).
        execute: winner 유지 + loser.status='archived' + loser.merged_into_trace_id=winner_id
        + leaves_to_reassign 의 DynamicLeafTopicCSOTopic 매핑 갱신.
        return: 실행된 MergePlan list (실행 안 한 후보는 LLM 거부 또는 conflict).
        """
        ...

    async def create_new_trace(
        self,
        user_id: UUID,
        active_day_counter: int,
        root_cso_topic_id: UUID,
        *,
        origin: str = "behavioral",
    ) -> UUID:
        """사용자 첫 카드 클릭 시 cold-start trace 생성 (결정 #6).

        A6 ingest_event_atomic hook 이 매칭 trace 없을 시 호출.
        active_cap (TRACE_ACTIVE_CAP, C-62 라운드 20) 초과 시 LEAF_TRAVERSAL_ACTIVE_CAP_EXCEEDED
        응답 (가장 idle stale archive).
        (C-62) origin arg — 'behavioral' default, 'onboarding_boost'/'weekly_promotion' 선택.
        path = [root_cso_topic_id], status='active'.
        return: 생성된 trace_id.
        """
        ...

    # --- read (A6 propagation + A8 추천 의존) ---

    async def get_active_traces(
        self,
        user_id: UUID,
    ) -> list[UserCSOTraversal]:
        """사용자의 모든 active trace. A6 propagation 이 path 위 조상 list 결정 시 호출.

        결정 매트릭스 #5: INTEREST_PROPAGATION_ENABLED=true 토글 후 본 메서드 사용.
        """
        ...

    async def get_current_topics(
        self,
        user_id: UUID,
    ) -> list[UUID]:
        """모든 active trace 의 path 끝 노드 cso_topic_id list (core 카테고리 후보).

        A8 추천 core 슬롯 (5개) 후보.
        """
        ...

    async def get_adjacent_topics(
        self,
        user_id: UUID,
    ) -> list[UUID]:
        """path 끝 노드의 1-hop 그래프 이웃 cso_topic_id list (adjacent 후보).

        A8 추천 adjacent 슬롯 (3개) 후보. NetworkX 캐시 (app.topic.graph) 사용.
        """
        ...

    async def get_descendant_leaves(
        self,
        trace_id: UUID,
    ) -> list[DynamicLeafTopic]:
        """trace.path 산하 (cso_topic_ids 매핑) leaf list.

        status IN ('active','emerging') — merged/archived 제외 (결정 #16).
        A8 추천 core 슬롯 의 leaf 후보.
        """
        ...

    async def get_emerging_leaves(
        self,
        user_id: UUID,
    ) -> list[DynamicLeafTopic]:
        """사용자의 모든 emerging leaf (status='emerging').

        A8 core 슬롯 5 중 1 emerging quota (decisions.md §4) 후보.
        """
        ...


# ============================================================
# 보조 dataclass — orchestrator 가 batch 결과 반환 시
# ============================================================


@dataclass(slots=True)
class TraversalBatchResult:
    """daily cron batch 처리 결과 (worker job 반환값)."""

    users_processed: int = 0
    stale_marked: int = 0       # 1단계 강등
    retracted: int = 0          # 2단계 강등 (LLM 호출)
    archived: int = 0           # 3단계 강등
    merged: int = 0             # trace merge (LLM 호출)
    leaves_promoted: int = 0    # emerging → active
    leaves_demoted: int = 0     # active → stale / stale → archived
    leaves_reactivated: int = 0
    llm_calls: int = 0
    failures: list[str] = field(default_factory=list)


__all__ = [
    "MergePlan",
    "NoOp",
    "RetractPlan",
    "SplitPlan",
    "TraversalBatchResult",
    "TraversalDelta",
    "TraversalEngine",
]
