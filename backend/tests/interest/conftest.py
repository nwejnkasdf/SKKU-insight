"""A6 interest 테스트 fixture — system_config seed + user + CSO/Document."""
from __future__ import annotations

import uuid
from datetime import date

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CSOTopic,
    Document,
    DocumentTopic,
    Source,
    SystemConfig,
    User,
)


@pytest_asyncio.fixture
async def seeded_user(db_session: AsyncSession) -> User:
    """test User 1명 (consent 활성 X — interest service 가 user 객체만 받음)."""
    user = User(
        user_id=uuid.uuid4(),
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="dummy-hash",
        onboarding_complete=True,
        active_day_counter=1,
        last_active_calendar_date=date.today(),
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def seeded_cso_topics(db_session: AsyncSession) -> list[CSOTopic]:
    """3개 CSO 토픽 (NLP, ML, RAG-leaf-parent)."""
    topics: list[CSOTopic] = []
    for label in ("test-nlp", "test-ml", "test-cv"):
        t = CSOTopic(
            cso_topic_id=uuid.uuid4(),
            label=label,
            uri=f"http://test/{label}",
            cluster_labels=["AI"],
            depth_from_seed=1,
        )
        topics.append(t)
        db_session.add(t)
    await db_session.flush()
    return topics


@pytest_asyncio.fixture
async def seeded_source(db_session: AsyncSession) -> Source:
    """sentinel `llm_search` source (alembic 0003 시드)."""
    row = (
        await db_session.execute(
            select(Source).where(Source.name == "llm_search")
        )
    ).scalars().first()
    if row is not None:
        return row
    # fallback: 직접 INSERT
    src = Source(
        source_id=uuid.uuid4(),
        name="test-source",
        source_type="vendor_blog",
        url="internal://test",
        trust_level="medium",
        enabled=True,
    )
    db_session.add(src)
    await db_session.flush()
    return src


@pytest_asyncio.fixture
async def seeded_document(
    db_session: AsyncSession,
    seeded_source: Source,
    seeded_cso_topics: list[CSOTopic],
) -> Document:
    """Document 1개 + DocumentTopic 매핑 3개 (cso, 각 confidence)."""
    doc = Document(
        document_id=uuid.uuid4(),
        source_id=seeded_source.source_id,
        title="Test Document",
        normalized_title="test document",
        url="https://test.example.com/doc",
        content_type="vendor_blog",
    )
    db_session.add(doc)
    await db_session.flush()
    # DocumentTopic 3개 — confidence 0.6, 0.3, 0.1
    for topic, conf in zip(seeded_cso_topics, (0.6, 0.3, 0.1), strict=True):
        dt = DocumentTopic(
            id=uuid.uuid4(),
            document_id=doc.document_id,
            cso_topic_id=topic.cso_topic_id,
            leaf_topic_id=None,
            confidence=conf,
        )
        db_session.add(dt)
    await db_session.flush()
    return doc


@pytest_asyncio.fixture
async def seeded_system_config(db_session: AsyncSession) -> None:
    """system_config seed (alembic 0004 의 default 행 — 테스트 격리 시 INSERT)."""
    existing = (
        await db_session.execute(
            select(SystemConfig.key).where(
                SystemConfig.key.in_(["interest_params", "event_weights"])
            )
        )
    ).all()
    existing_keys = {row.key for row in existing}
    if "interest_params" not in existing_keys:
        db_session.add(
            SystemConfig(
                key="interest_params",
                value={
                    "alpha_prior": 1.0,
                    "beta_prior": 4.0,
                    "half_life_short_active_days": 7,
                    "half_life_long_active_days": 60,
                    "onboarding_prior_boost": 1.0,
                    "onboarding_boost_active_days": 14,
                    "propagation_hop_decay": 0.5,
                    "propagation_max_hops": 4,
                    "propagation_non_trace_ancestors": False,
                    "bucket_high_long": 0.70,
                    "bucket_high_short": 0.60,
                    "bucket_medium": 0.50,
                    "bucket_low": 0.30,
                },
                description="test seed",
            )
        )
    if "event_weights" not in existing_keys:
        db_session.add(
            SystemConfig(
                key="event_weights",
                value={
                    "weights": {
                        "view": 0.0,
                        "click": 1.0,
                        "dwell_tick": 0.5,
                        "open_external": 2.0,
                        "save": 5.0,
                        "hide": -3.0,
                        "not_interested": -5.0,
                    },
                    "caps": {
                        "dwell_tick_max_per_document": 4,
                        "weight_per_event_max": 5.0,
                    },
                },
                description="test seed",
            )
        )
    await db_session.flush()
