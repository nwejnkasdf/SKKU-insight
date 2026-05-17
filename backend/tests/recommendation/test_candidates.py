"""candidates SQL 정확성 — core/adjacent/discovery + AntiJoin 6종."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ClickbaitResult,
    Document,
    DocumentTopic,
    HiddenDocument,
    NotInterestedTopic,
    SavedDocument,
    User,
)
from app.recommendation.candidates import (
    query_adjacent,
    query_core,
    query_discovery,
    query_emerging_leaf_documents,
)


@pytest.mark.asyncio
async def test_core_returns_docs_matching_current_csos(
    db_session: AsyncSession,
    rec_user: User,
    rec_cso_topics,
    rec_documents,
) -> None:
    """current_csos 매핑 Document 모두 반환."""
    current_csos = [rec_cso_topics[0].cso_topic_id, rec_cso_topics[1].cso_topic_id]
    rows = await query_core(db_session, rec_user.user_id, current_csos, [])
    doc_ids = {r.document_id for r in rows}
    # rec_documents[0] (rec-A), [1] (rec-B-child) 매핑된 doc 만 포함.
    assert rec_documents[0].document_id in doc_ids
    assert rec_documents[1].document_id in doc_ids
    # rec_documents[2] (rec-C-adjacent), [3] (rec-D-discovery) 는 매핑된 cso 가 current 아님.
    assert rec_documents[2].document_id not in doc_ids
    assert rec_documents[3].document_id not in doc_ids


@pytest.mark.asyncio
async def test_core_antijoin_excludes_saved(
    db_session: AsyncSession,
    rec_user: User,
    rec_cso_topics,
    rec_documents,
) -> None:
    """SavedDocument 에 있는 Document 는 core 결과 제외."""
    saved = SavedDocument(
        user_id=rec_user.user_id,
        document_id=rec_documents[0].document_id,
    )
    db_session.add(saved)
    await db_session.flush()
    current_csos = [rec_cso_topics[0].cso_topic_id]
    rows = await query_core(db_session, rec_user.user_id, current_csos, [])
    doc_ids = {r.document_id for r in rows}
    assert rec_documents[0].document_id not in doc_ids


@pytest.mark.asyncio
async def test_core_antijoin_excludes_hidden_and_clickbait(
    db_session: AsyncSession,
    rec_user: User,
    rec_cso_topics,
    rec_documents,
) -> None:
    """HiddenDocument 또는 ClickbaitResult.decision='clickbait' 모두 제외."""
    db_session.add(
        HiddenDocument(
            user_id=rec_user.user_id, document_id=rec_documents[0].document_id
        )
    )
    db_session.add(
        ClickbaitResult(
            result_id=uuid.uuid4(),
            document_id=rec_documents[1].document_id,
            model_name="test",
            adapter_type="test",
            decision="clickbait",
            confidence=0.95,
            evaluated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()
    current_csos = [
        rec_cso_topics[0].cso_topic_id, rec_cso_topics[1].cso_topic_id
    ]
    rows = await query_core(db_session, rec_user.user_id, current_csos, [])
    doc_ids = {r.document_id for r in rows}
    assert rec_documents[0].document_id not in doc_ids   # hidden
    assert rec_documents[1].document_id not in doc_ids   # clickbait


@pytest.mark.asyncio
async def test_core_antijoin_excludes_not_interested_topic(
    db_session: AsyncSession,
    rec_user: User,
    rec_cso_topics,
    rec_documents,
) -> None:
    """NotInterestedTopic 에 매핑된 cso_topic 의 Document 제외."""
    db_session.add(
        NotInterestedTopic(
            id=uuid.uuid4(),
            user_id=rec_user.user_id,
            cso_topic_id=rec_cso_topics[0].cso_topic_id,
            leaf_topic_id=None,
        )
    )
    await db_session.flush()
    current_csos = [rec_cso_topics[0].cso_topic_id]
    rows = await query_core(db_session, rec_user.user_id, current_csos, [])
    doc_ids = {r.document_id for r in rows}
    assert rec_documents[0].document_id not in doc_ids


@pytest.mark.asyncio
async def test_core_excludes_pseudo_cold_start(
    db_session: AsyncSession,
    rec_user: User,
    rec_source,
    rec_cso_topics,
) -> None:
    """content_type='pseudo_cold_start' Document 는 일반 추천 경로에서 자동 제외."""
    pseudo = Document(
        document_id=uuid.uuid4(),
        source_id=rec_source.source_id,
        title="Pseudo Doc",
        normalized_title="pseudo doc",
        url="internal://pseudo",
        content_type="pseudo_cold_start",
    )
    db_session.add(pseudo)
    await db_session.flush()
    db_session.add(
        DocumentTopic(
            id=uuid.uuid4(),
            document_id=pseudo.document_id,
            cso_topic_id=rec_cso_topics[0].cso_topic_id,
            leaf_topic_id=None,
            confidence=0.9,
        )
    )
    await db_session.flush()
    current_csos = [rec_cso_topics[0].cso_topic_id]
    rows = await query_core(db_session, rec_user.user_id, current_csos, [])
    doc_ids = {r.document_id for r in rows}
    assert pseudo.document_id not in doc_ids


@pytest.mark.asyncio
async def test_adjacent_excludes_current_csos(
    db_session: AsyncSession,
    rec_user: User,
    rec_cso_topics,
    rec_documents,
) -> None:
    """adjacent_csos 매핑이라도 current_csos 와 겹치면 제외 (race 가드)."""
    adjacent_csos = [
        rec_cso_topics[1].cso_topic_id, rec_cso_topics[2].cso_topic_id
    ]
    current_csos = [rec_cso_topics[1].cso_topic_id]
    rows = await query_adjacent(
        db_session, rec_user.user_id, adjacent_csos, current_csos
    )
    doc_ids = {r.document_id for r in rows}
    # rec_documents[1] 은 B-child 매핑 — current 와 겹쳐서 제외.
    assert rec_documents[1].document_id not in doc_ids
    # rec_documents[2] 은 C-adjacent 매핑 — 통과.
    assert rec_documents[2].document_id in doc_ids


@pytest.mark.asyncio
async def test_discovery_filters_trust_high(
    db_session: AsyncSession,
    rec_user: User,
    rec_cso_topics,
    rec_documents,
) -> None:
    """discovery 는 trust_level='high' source 만. 본 fixture 의 rec_source 가 high."""
    excluded = [rec_cso_topics[0].cso_topic_id]   # current
    rows = await query_discovery(db_session, rec_user.user_id, excluded)
    doc_ids = {r.document_id for r in rows}
    # rec_documents[0] (rec-A 매핑) 은 current 제외.
    assert rec_documents[0].document_id not in doc_ids
    # rec_documents[1~3] 는 current 외 — 통과 가능.
    assert rec_documents[3].document_id in doc_ids


@pytest.mark.asyncio
async def test_emerging_pool_only_emerging_status(
    db_session: AsyncSession,
    rec_user: User,
    rec_cso_topics,
    rec_documents,
    rec_leaves,
) -> None:
    """emerging quota pool 은 leaf.status='emerging' row 만."""
    # rec_documents[1] 을 emerging leaf 에 매핑 추가.
    db_session.add(
        DocumentTopic(
            id=uuid.uuid4(),
            document_id=rec_documents[1].document_id,
            cso_topic_id=None,
            leaf_topic_id=rec_leaves[1].leaf_topic_id,   # emerging
            confidence=0.85,
        )
    )
    await db_session.flush()
    rows = await query_emerging_leaf_documents(
        db_session,
        rec_user.user_id,
        [rec_leaves[0].leaf_topic_id, rec_leaves[1].leaf_topic_id],
    )
    # emerging leaf 에 매핑된 doc 만.
    doc_ids = {r.document_id for r in rows}
    assert rec_documents[1].document_id in doc_ids
    # leaf_status 컬럼 = 'emerging' (단일 SQL fetch 검증)
    matching = [r for r in rows if r.document_id == rec_documents[1].document_id]
    assert any(r.leaf_status == "emerging" for r in matching)
