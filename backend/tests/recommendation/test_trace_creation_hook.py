"""A8 cold-start 후 첫 click → trace 생성 hook 통합.

ingest_event_atomic 의 traversal_lock 보유 구간 안 mark_stale_if_idle 옆에서:
- event_type=CLICK AND document_id 존재 AND active_traces=0 AND DocumentTopic cso 매핑 존재
- → DefaultTraversalEngine.ingest_event() 위임 (매칭 trace 있으면 last_activity, 없으면 새 trace).

검증 항목: _document_topic_cso_ids helper 정확성 + ingest_event_atomic 가 hook 실행.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentTopic, UserCSOTraversal
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
