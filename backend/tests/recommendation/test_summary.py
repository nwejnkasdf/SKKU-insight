"""DocumentSummaryCache + summary_service LLM 통합."""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import DocumentSummaryCache
from app.llm_provider.protocol import LLMResponse, ProviderError
from app.recommendation.summary_service import get_or_build_summary


def _resp(parsed: Any) -> LLMResponse:
    return LLMResponse(
        text=json.dumps(parsed, ensure_ascii=False),
        model="mock-sum",
        prompt_tokens=10,
        completion_tokens=20,
        parsed_json=parsed,
    )


@pytest.mark.asyncio
async def test_summary_cache_hit_skips_llm(
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
    rec_documents,
    mock_summary_response: dict[str, Any],
) -> None:
    """DB cache hit → LLM 호출 0회."""
    doc = rec_documents[0]
    # cache row pre-INSERT.
    db_session.add(
        DocumentSummaryCache(
            document_id=doc.document_id,
            sections=mock_summary_response["sections"],
            reason_short="기존 요약",
            model_used="prior-run",
            generator="llm",
        )
    )
    await db_session.flush()
    provider = AsyncMock()
    provider.complete = AsyncMock()
    settings = get_settings()
    resp = await get_or_build_summary(
        db_session, redis_client, provider, settings, doc.document_id
    )
    assert resp.generator == "llm"
    assert len(resp.sections) == 4
    # LLM 호출 안 했음을 검증.
    provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_summary_cache_miss_calls_llm_and_inserts(
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
    rec_documents,
    mock_summary_response: dict[str, Any],
) -> None:
    """cache miss → LLM complete 호출 + INSERT row."""
    doc = rec_documents[0]
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_resp(mock_summary_response))
    settings = get_settings()
    resp = await get_or_build_summary(
        db_session, redis_client, provider, settings, doc.document_id
    )
    assert resp.generator == "llm"
    assert len(resp.sections) == 4
    provider.complete.assert_called_once()


@pytest.mark.asyncio
async def test_summary_404_when_document_not_exist(
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
) -> None:
    """존재하지 않는 document_id → 404."""
    provider = AsyncMock()
    settings = get_settings()
    with pytest.raises(HTTPException) as exc_info:
        await get_or_build_summary(
            db_session, redis_client, provider, settings, uuid.uuid4()
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_summary_provider_error_with_summary_falls_back(
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
    rec_source,
) -> None:
    """ProviderError + Document.summary 존재 → generator='source_abstract'."""
    from app.db.models import Document

    doc = Document(
        document_id=uuid.uuid4(),
        source_id=rec_source.source_id,
        title="Doc",
        normalized_title="doc",
        url="https://test/doc",
        summary="이 문서는 테스트용 요약입니다. " * 5,
        content_type="academic_paper",
    )
    db_session.add(doc)
    await db_session.flush()
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=ProviderError("fail"))
    settings = get_settings()
    resp = await get_or_build_summary(
        db_session, redis_client, provider, settings, doc.document_id
    )
    assert resp.generator == "source_abstract"
    assert len(resp.sections) == 1
    assert resp.sections[0].section == "core"


@pytest.mark.asyncio
async def test_summary_provider_error_no_summary_503(
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
    rec_source,
) -> None:
    """ProviderError + Document.summary 없음 → 503."""
    from app.db.models import Document

    doc = Document(
        document_id=uuid.uuid4(),
        source_id=rec_source.source_id,
        title="Doc",
        normalized_title="doc",
        url="https://test/doc2",
        summary=None,
        content_type="academic_paper",
    )
    db_session.add(doc)
    await db_session.flush()
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=ProviderError("fail"))
    settings = get_settings()
    with pytest.raises(HTTPException) as exc_info:
        await get_or_build_summary(
            db_session, redis_client, provider, settings, doc.document_id
        )
    assert exc_info.value.status_code == 503
