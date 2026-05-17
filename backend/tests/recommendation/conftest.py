"""A8 recommendation 테스트 fixture.

- seeded_user/cso/source/document: interest conftest 패턴 재활용 — 본 모듈은 추가 fixture.
- seeded_traversal: active UserCSOTraversal 1개 (path=[cso_a, cso_b]).
- seeded_leaves: DynamicLeafTopic 2개 (active + emerging) + 매핑.
- mock_llm_cold_start: 10 후보 JSON fixture (5/3/2).
- mock_llm_reasons: reasons dict response.
- mock_llm_summary: 4 sections JSON response.
- mock_llm_provider: LLMProvider stub — complete() 호출 시 fixture 반환.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import LeafTopicStatus, TraversalStatus
from app.db.models import (
    CSOTopic,
    Document,
    DocumentTopic,
    DynamicLeafTopic,
    DynamicLeafTopicCSOTopic,
    Source,
    User,
    UserCSOTraversal,
)
from app.llm_provider.protocol import LLMResponse


@pytest_asyncio.fixture
async def rec_user(db_session: AsyncSession) -> User:
    """test User (active_day=5, consent 활성 — 추천 endpoint 호출 가능)."""
    user = User(
        user_id=uuid.uuid4(),
        email=f"rec-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="dummy-hash",
        onboarding_complete=True,
        active_day_counter=5,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def rec_cso_topics(db_session: AsyncSession) -> list[CSOTopic]:
    """4개 CSO 토픽 — A/B(child of A)/C(adjacent of B)/D(discovery 영역)."""
    topics: list[CSOTopic] = []
    for label in ("rec-A", "rec-B-child", "rec-C-adjacent", "rec-D-discovery"):
        t = CSOTopic(
            cso_topic_id=uuid.uuid4(),
            label=label,
            uri=f"http://test-rec/{label}",
            cluster_labels=["AI"],
        )
        topics.append(t)
        db_session.add(t)
    await db_session.flush()
    return topics


@pytest_asyncio.fixture
async def rec_source(db_session: AsyncSession) -> Source:
    """trust_level=high test source — discovery slot 통과용."""
    src_name = f"rec-test-source-{uuid.uuid4().hex[:6]}"
    src = Source(
        source_id=uuid.uuid4(),
        name=src_name,
        source_type="vendor_blog",
        url=f"internal://{src_name}",
        trust_level="high",
        enabled=True,
    )
    db_session.add(src)
    await db_session.flush()
    return src


@pytest_asyncio.fixture
async def rec_cold_start_sentinel(db_session: AsyncSession) -> Source:
    """cold_start_pseudo sentinel — alembic 0001 시드 (이미 있음). 없으면 INSERT."""
    row = (
        await db_session.execute(
            select(Source).where(Source.name == "cold_start_pseudo")
        )
    ).scalars().first()
    if row is not None:
        return row
    src = Source(
        source_id=uuid.uuid4(),
        name="cold_start_pseudo",
        source_type="vendor_blog",
        url="internal://cold-start-pseudo",
        trust_level="low",
        enabled=False,
    )
    db_session.add(src)
    await db_session.flush()
    return src


@pytest_asyncio.fixture
async def rec_documents(
    db_session: AsyncSession,
    rec_source: Source,
    rec_cso_topics: list[CSOTopic],
) -> list[Document]:
    """4개 Document — 각각 다른 CSO 매핑. 최근 24h published_at."""
    now = datetime.now(UTC)
    docs: list[Document] = []
    for i, topic in enumerate(rec_cso_topics):
        doc = Document(
            document_id=uuid.uuid4(),
            source_id=rec_source.source_id,
            title=f"Test Doc {i} on {topic.label}",
            normalized_title=f"test doc {i}",
            url=f"https://test.example.com/doc-{i}",
            canonical_url=f"https://test.example.com/doc-{i}",
            content_type="academic_paper",
            published_at=now - timedelta(hours=i * 2),
        )
        db_session.add(doc)
        await db_session.flush()
        dt = DocumentTopic(
            id=uuid.uuid4(),
            document_id=doc.document_id,
            cso_topic_id=topic.cso_topic_id,
            leaf_topic_id=None,
            confidence=0.9,
        )
        db_session.add(dt)
        docs.append(doc)
    await db_session.flush()
    return docs


@pytest_asyncio.fixture
async def rec_traversal(
    db_session: AsyncSession,
    rec_user: User,
    rec_cso_topics: list[CSOTopic],
) -> UserCSOTraversal:
    """active trace — path=[A, B-child]. last_activity_active_day=5."""
    trace = UserCSOTraversal(
        trace_id=uuid.uuid4(),
        user_id=rec_user.user_id,
        path=[rec_cso_topics[0].cso_topic_id, rec_cso_topics[1].cso_topic_id],
        status=TraversalStatus.ACTIVE.value,
        started_active_day=1,
        last_activity_active_day=5,
        score_tail=0.5,
    )
    db_session.add(trace)
    await db_session.flush()
    return trace


@pytest_asyncio.fixture
async def rec_leaves(
    db_session: AsyncSession,
    rec_user: User,
    rec_cso_topics: list[CSOTopic],
) -> list[DynamicLeafTopic]:
    """2개 leaf — active (B-child 산하) + emerging (B-child 산하)."""
    active_leaf = DynamicLeafTopic(
        leaf_topic_id=uuid.uuid4(),
        user_id=rec_user.user_id,
        label="rec-active-leaf",
        confidence=0.8,
        status=LeafTopicStatus.ACTIVE.value,
        created_active_day=1,
        last_signal_active_day=5,
    )
    emerging_leaf = DynamicLeafTopic(
        leaf_topic_id=uuid.uuid4(),
        user_id=rec_user.user_id,
        label="rec-emerging-leaf",
        confidence=0.7,
        status=LeafTopicStatus.EMERGING.value,
        created_active_day=3,
        last_signal_active_day=5,
    )
    db_session.add_all([active_leaf, emerging_leaf])
    await db_session.flush()
    # 매핑 — 둘 다 B-child 산하.
    for leaf in (active_leaf, emerging_leaf):
        mapping = DynamicLeafTopicCSOTopic(
            leaf_topic_id=leaf.leaf_topic_id,
            cso_topic_id=rec_cso_topics[1].cso_topic_id,
            confidence=0.9,
        )
        db_session.add(mapping)
    await db_session.flush()
    return [active_leaf, emerging_leaf]


# ============================================================
# Mock LLM provider — cold-start / reasons / summary
# ============================================================


@pytest.fixture
def mock_cold_start_response() -> dict[str, Any]:
    """LLM cold-start 10 후보 (5 core / 3 adjacent / 2 discovery)."""
    return {
        "items": [
            # core 5
            *[
                {
                    "slot_type": "core",
                    "title": f"Core Article {i}",
                    "title_ko": f"코어 글 {i}",
                    "publisher_domain": "arxiv.org",
                    "publisher_label": "arXiv",
                    "source_type": "academic",
                    "url_hint": f"https://arxiv.org/abs/240{i}.{i:05d}",
                    "doi_hint": None,
                    "published_year": 2025,
                    "related_csos_en": ["Computer Vision"],
                    "reason_short_ko": f"코어 토픽 관련 신규 자료 {i}",
                }
                for i in range(5)
            ],
            # adjacent 3
            *[
                {
                    "slot_type": "adjacent",
                    "title": f"Adjacent Article {i}",
                    "title_ko": f"인접 글 {i}",
                    "publisher_domain": "openai.com",
                    "publisher_label": "OpenAI Blog",
                    "source_type": "vendor_blog",
                    "url_hint": f"https://openai.com/blog/{i}",
                    "doi_hint": None,
                    "published_year": 2025,
                    "related_csos_en": ["NLP"],
                    "reason_short_ko": f"인접 분야 자료 {i}",
                }
                for i in range(3)
            ],
            # discovery 2
            *[
                {
                    "slot_type": "discovery",
                    "title": f"Discovery Article {i}",
                    "title_ko": f"탐색 글 {i}",
                    "publisher_domain": "techcrunch.com",
                    "publisher_label": "TechCrunch",
                    "source_type": "tech_news",
                    "url_hint": f"https://techcrunch.com/news/{i}",
                    "doi_hint": None,
                    "published_year": 2025,
                    "related_csos_en": ["Robotics"],
                    "reason_short_ko": f"잠재 흥미 자료 {i}",
                }
                for i in range(2)
            ],
        ]
    }


@pytest.fixture
def mock_summary_response() -> dict[str, Any]:
    """LLM 4 섹션 (core/background/significance/limitations) + reason_short."""
    return {
        "sections": [
            {"section": "core", "title_ko": "핵심", "body_ko": "본 논문은 ..."},
            {"section": "background", "title_ko": "배경", "body_ko": "이전 연구는 ..."},
            {"section": "significance", "title_ko": "중요도", "body_ko": "본 결과는 ..."},
            {"section": "limitations", "title_ko": "한계", "body_ko": "다만 ..."},
        ],
        "reason_short_ko": "본 논문은 새로운 접근법을 제안합니다",
    }


@pytest.fixture
def mock_reasons_response() -> dict[str, Any]:
    """LLM reasons dict — recommendation_id → reason_short."""
    return {"reasons": []}   # caller 가 document_id 별로 채움


def _make_llm_response(parsed: Any, model: str = "mock-gpt") -> LLMResponse:
    """LLMResponse 헬퍼."""
    import json
    text = json.dumps(parsed, ensure_ascii=False)
    return LLMResponse(
        text=text,
        model=model,
        prompt_tokens=100,
        completion_tokens=200,
        finish_reason="stop",
        parsed_json=parsed,
    )


@pytest.fixture
def mock_llm_provider_cold_start(
    mock_cold_start_response: dict[str, Any],
) -> Any:
    """cold-start LLM call 만 mock — complete() 호출 시 fixture 반환."""
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=_make_llm_response(mock_cold_start_response)
    )
    return provider


@pytest.fixture
def mock_llm_provider_summary(
    mock_summary_response: dict[str, Any],
) -> Any:
    """summary LLM call 만 mock."""
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=_make_llm_response(mock_summary_response)
    )
    return provider


@pytest.fixture
def mock_llm_provider_reasons_factory() -> Any:
    """reasons LLM call mock — 호출 시 인자 document_id 들 추출해 응답 생성.

    실제 테스트에서 cards list 의 document_id 를 사용해 valid response 만들 수 있도록 helper.
    """

    def _make(document_ids: list[uuid.UUID]) -> Any:
        provider = AsyncMock()
        reasons_resp = {
            "reasons": [
                {"document_id": str(did), "reason_short_ko": f"테스트 토픽 {i}"}
                for i, did in enumerate(document_ids)
            ]
        }
        provider.complete = AsyncMock(
            return_value=_make_llm_response(reasons_resp)
        )
        return provider

    return _make
