"""A8 cold-start 후 첫 engagement → trace 생성 hook 통합.

ingest_event_atomic 의 traversal_lock 보유 구간 안 mark_stale_if_idle 옆에서:
- event_type ∈ _TRACE_CREATION_EVENT_TYPES ({CLICK, SAVE, DWELL_TICK}) AND
  document_id 존재 AND DocumentTopic cso 매핑 존재
- → DefaultTraversalEngine.ingest_event() 위임 (매칭 trace 있으면 last_activity, 없으면 새 trace).

검증 항목: _document_topic_cso_ids helper 정확성 + ingest_event_atomic 가 hook 실행.
"""
from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import EventType
from app.db.models import DocumentTopic, UserCSOTraversal
from app.interest import service
from app.interest.service import _document_topic_cso_ids


@pytest.mark.asyncio
async def test_document_topic_cso_ids_orders_by_confidence(
    db_session: AsyncSession,
    rec_documents,
    rec_cso_topics,
) -> None:
    """confidence DESC 순서로 cso_topic_ids 반환 — 첫 번째가 가장 confident."""
    doc = rec_documents[0]
    # 기존 매핑은 fixture 가 confidence=0.9 1개. 추가 매핑 2개 INSERT.
    db_session.add(
        DocumentTopic(
            id=uuid.uuid4(),
            document_id=doc.document_id,
            cso_topic_id=rec_cso_topics[1].cso_topic_id,
            leaf_topic_id=None,
            confidence=0.7,
        )
    )
    db_session.add(
        DocumentTopic(
            id=uuid.uuid4(),
            document_id=doc.document_id,
            cso_topic_id=rec_cso_topics[2].cso_topic_id,
            leaf_topic_id=None,
            confidence=0.95,
        )
    )
    await db_session.flush()
    cso_ids = await _document_topic_cso_ids(db_session, doc.document_id)
    # 3개 — confidence DESC. 첫째 = 0.95 (topics[2]), 둘째 = 0.9 (topics[0], fixture), 셋째 = 0.7.
    assert len(cso_ids) == 3
    assert cso_ids[0] == rec_cso_topics[2].cso_topic_id
    assert cso_ids[1] == rec_cso_topics[0].cso_topic_id
    assert cso_ids[2] == rec_cso_topics[1].cso_topic_id


@pytest.mark.asyncio
async def test_document_topic_cso_ids_skips_null(
    db_session: AsyncSession,
    rec_documents,
    rec_leaves,
) -> None:
    """cso_topic_id IS NULL row (leaf-only 매핑) 자동 제외."""
    doc = rec_documents[0]
    # leaf-only 매핑 추가 (cso=NULL).
    db_session.add(
        DocumentTopic(
            id=uuid.uuid4(),
            document_id=doc.document_id,
            cso_topic_id=None,
            leaf_topic_id=rec_leaves[0].leaf_topic_id,
            confidence=0.99,
        )
    )
    await db_session.flush()
    cso_ids = await _document_topic_cso_ids(db_session, doc.document_id)
    # fixture 의 cso_topic_id=non-null 1개만 반환 — leaf-only row 제외.
    assert len(cso_ids) == 1
    assert all(cid is not None for cid in cso_ids)


@pytest.mark.asyncio
async def test_document_topic_cso_ids_empty_for_unknown_doc(
    db_session: AsyncSession,
) -> None:
    """존재하지 않는 document_id → 빈 list (hook 가 trace 생성 skip)."""
    cso_ids = await _document_topic_cso_ids(db_session, uuid.uuid4())
    assert cso_ids == []


@pytest.mark.asyncio
async def test_traversal_engine_creates_new_trace_when_no_match(
    db_session: AsyncSession,
    rec_user,
    rec_cso_topics,
    redis_client,
) -> None:
    """DefaultTraversalEngine.ingest_event — 매칭 trace 없을 시 새 trace 생성 (path=[cso_id])."""
    # 빈 NetworkX graph (test 단순화 — extend 등 graph 의존 path X)
    import networkx as nx

    from app.llm_provider.mock import MockProvider
    from app.traversal.default import DefaultTraversalEngine

    graph = nx.DiGraph()
    # cso_topic 노드 1개만 그래프에 추가 (engine 이 lookup 가능).
    cso_id = rec_cso_topics[0].cso_topic_id
    graph.add_node(cso_id, label="test", uri="http://t", cluster_labels=set())

    engine = DefaultTraversalEngine(db_session, MockProvider(), graph)
    delta = await engine.ingest_event(
        rec_user.user_id, active_day_counter=5, cso_topic_ids=[cso_id]
    )
    # 매칭 trace 없으니 새 trace 생성.
    assert delta == "new_trace"
    # DB 검증.
    traces = (
        await db_session.execute(
            select(UserCSOTraversal).where(
                UserCSOTraversal.user_id == rec_user.user_id
            )
        )
    ).scalars().all()
    assert len(traces) == 1
    assert traces[0].path == [cso_id]
    assert traces[0].status == "active"


@pytest.mark.asyncio
async def test_traversal_engine_noop_when_match(
    db_session: AsyncSession,
    rec_user,
    rec_cso_topics,
    rec_traversal,
) -> None:
    """매칭 active trace 있으면 ingest_event = 'noop' (last_activity 만 갱신)."""
    import networkx as nx

    from app.llm_provider.mock import MockProvider
    from app.traversal.default import DefaultTraversalEngine

    graph = nx.DiGraph()
    for cso in rec_cso_topics:
        graph.add_node(cso.cso_topic_id, label=cso.label, uri=cso.uri, cluster_labels=set())

    engine = DefaultTraversalEngine(db_session, MockProvider(), graph)
    # rec_traversal.path = [cso_topics[0], cso_topics[1]] 이미 fixture 가 만듦.
    delta = await engine.ingest_event(
        rec_user.user_id,
        active_day_counter=6,
        cso_topic_ids=[rec_cso_topics[1].cso_topic_id],   # path 끝 노드
    )
    assert delta == "noop"
    # 새 trace 생성 안 됨 — 여전히 1개.
    traces = (
        await db_session.execute(
            select(UserCSOTraversal).where(
                UserCSOTraversal.user_id == rec_user.user_id
            )
        )
    ).scalars().all()
    assert len(traces) == 1


class TestC61TraceCreationHookEventTypes:
    """C-61 회귀 가드 — trace creation hook trigger 가 click/save/dwell_tick 3종 모두 포함.

    cso-topic-traversal.md §3 "실제로 활동한 노드 (클릭/저장 등)" 명세 정합.
    A8 trace creation hook 이 다시 CLICK-only 로 좁아지지 않게 정적 source 검증.
    """

    def test_trace_creation_event_types_constant_exists(self) -> None:
        assert hasattr(service, "_TRACE_CREATION_EVENT_TYPES")

    def test_trace_creation_covers_click_save_dwell_tick(self) -> None:
        types = service._TRACE_CREATION_EVENT_TYPES
        assert EventType.CLICK.value in types
        assert EventType.SAVE.value in types
        assert EventType.DWELL_TICK.value in types

    def test_trace_creation_excludes_negative_signals(self) -> None:
        """HIDE / NOT_INTERESTED 는 부정 신호 — trace 관심 의미 반대라 제외."""
        types = service._TRACE_CREATION_EVENT_TYPES
        assert EventType.HIDE.value not in types
        assert EventType.NOT_INTERESTED.value not in types
        assert EventType.VIEW.value not in types

    def test_ingest_event_atomic_uses_trace_creation_constant(self) -> None:
        """hook 코드가 module 상수를 참조 — 다시 CLICK 단일 비교로 회귀 방지."""
        src = inspect.getsource(service.ingest_event_atomic)
        assert "_TRACE_CREATION_EVENT_TYPES" in src
        # 회귀 검출 — CLICK 단일 비교 패턴 (== EventType.CLICK) 잔재 없어야 함.
        # (다른 코드 경로의 정상 CLICK 사용은 OK — hook 가드 표현만 막음.)
        assert "event_type == EventType.CLICK" not in src
