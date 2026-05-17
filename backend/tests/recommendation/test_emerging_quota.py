"""emerging quota race 방어 — candidates SQL 단일 호출로 leaf_status 확보."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentTopic
from app.recommendation.candidates import query_emerging_leaf_documents


@pytest.mark.asyncio
async def test_emerging_pool_only_returns_emerging_status(
    db_session: AsyncSession,
    rec_user,
    rec_cso_topics,
    rec_documents,
    rec_leaves,
) -> None:
    """leaf_status='emerging' row 만. active leaf 는 emerging_pool 에서 제외."""
    # 두 leaf 모두 doc 매핑.
    db_session.add(
        DocumentTopic(
            id=uuid.uuid4(),
            document_id=rec_documents[0].document_id,
            cso_topic_id=None,
            leaf_topic_id=rec_leaves[0].leaf_topic_id,   # active
            confidence=0.85,
        )
    )
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
    # emerging_pool 호출 — emerging leaf id 만 전달.
    rows = await query_emerging_leaf_documents(
        db_session,
        rec_user.user_id,
        [rec_leaves[1].leaf_topic_id],
    )
    doc_ids = {r.document_id for r in rows}
    # emerging 매핑 doc 만.
    assert rec_documents[1].document_id in doc_ids
    assert rec_documents[0].document_id not in doc_ids
    # leaf_status 컬럼 = 'emerging' 검증 (race 방어 핵심).
    for r in rows:
        assert r.leaf_status == "emerging"


@pytest.mark.asyncio
async def test_emerging_pool_empty_when_no_ids(
    db_session: AsyncSession,
    rec_user,
) -> None:
    """emerging_leaf_ids 빈 list → 빈 결과 (즉시 반환, SQL 호출 X)."""
    rows = await query_emerging_leaf_documents(
        db_session, rec_user.user_id, []
    )
    assert rows == []
