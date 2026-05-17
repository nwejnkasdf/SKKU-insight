"""cold-start orchestrator — validate + pseudo Document + Recommendation INSERT."""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.contracts import RedisKey, SlotType
from app.db.models import (
    BroadInterest,
    Document,
    Recommendation,
    RecommendationSlot,
)
from app.llm_provider.protocol import LLMResponse, ProviderError
from app.recommendation.cold_start import (
    InvalidColdStartResponse,
    _validate_cold_start,
    run_cold_start,
)


def _make_resp(parsed: Any) -> LLMResponse:
    return LLMResponse(
        text=json.dumps(parsed, ensure_ascii=False),
        model="mock-cold",
        prompt_tokens=100,
        completion_tokens=200,
        parsed_json=parsed,
    )


def test_validate_valid_response(mock_cold_start_response: dict[str, Any]) -> None:
    """10개 + 5/3/2 분배 valid → ColdStartItem list 10개 반환."""
    items = _validate_cold_start(mock_cold_start_response)
    assert len(items) == 10
    assert sum(1 for i in items if i.slot_type == SlotType.CORE) == 5
    assert sum(1 for i in items if i.slot_type == SlotType.ADJACENT) == 3
    assert sum(1 for i in items if i.slot_type == SlotType.DISCOVERY) == 2


def test_validate_wrong_count_raises() -> None:
    """10개 아니면 InvalidColdStartResponse."""
    with pytest.raises(InvalidColdStartResponse):
        _validate_cold_start({"items": [{}] * 9})


def test_validate_wrong_distribution_raises(
    mock_cold_start_response: dict[str, Any],
) -> None:
    """5/3/2 아니면 InvalidColdStartResponse."""
    items = list(mock_cold_start_response["items"])
    items[0] = {**items[0], "slot_type": "adjacent"}   # core 4, adjacent 4, discovery 2
    with pytest.raises(InvalidColdStartResponse):
        _validate_cold_start({"items": items})


def test_validate_truncates_long_reason(
    mock_cold_start_response: dict[str, Any],
) -> None:
    """reason_short_ko >80자 → truncate (거부 X — LLM 환상 완화)."""
    items_raw = list(mock_cold_start_response["items"])
    items_raw[0] = {**items_raw[0], "reason_short_ko": "가" * 100}
    items = _validate_cold_start({"items": items_raw})
    assert len(items[0].reason_short_ko) <= 80


def test_validate_invalidates_bad_url(
    mock_cold_start_response: dict[str, Any],
) -> None:
    """url_hint 가 invalid URL 이면 None 으로 강등."""
    items_raw = list(mock_cold_start_response["items"])
    items_raw[0] = {**items_raw[0], "url_hint": "not-a-url"}
    items = _validate_cold_start({"items": items_raw})
    assert items[0].url_hint is None


@pytest.mark.asyncio
async def test_run_cold_start_persists_recommendations(
    db_engine,
    redis_client: aioredis.Redis,
    rec_user,
    rec_cso_topics,
    rec_cold_start_sentinel,
    mock_cold_start_response: dict[str, Any],
) -> None:
    """run_cold_start 가 Recommendation 10 + RecommendationSlot 3 INSERT + status='completed'."""
    # BroadInterest cluster_label seed — cold_start 의 _resolve_cluster_labels 가 사용.
    session_factory = async_sessionmaker(bind=db_engine)
    async with session_factory() as session:
        # 별도 트랜잭션으로 user 와 broad_interest 시드 — run_cold_start 가 자체 session 사용.
        existing = (
            await session.execute(
                select(rec_user.__class__).where(
                    rec_user.__class__.user_id == rec_user.user_id
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(rec_user)
        bi = BroadInterest(
            broad_interest_id=uuid.uuid4(),
            name=f"test-bi-{uuid.uuid4().hex[:6]}",
            description="test cluster",
            cso_cluster_label="AI",
            cso_seed_topic_id=rec_cso_topics[0].cso_topic_id,
            display_order=0,
        )
        session.add(bi)
        await session.commit()
        bi_id = bi.broad_interest_id
        user_id = rec_user.user_id

    # mock provider.
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=_make_resp(mock_cold_start_response)
    )
    settings = get_settings()
    request_id = str(uuid.uuid4())

    try:
        await run_cold_start(
            session_factory,
            redis_client,
            provider,
            settings,
            request_id=request_id,
            user_id=str(user_id),
            cluster_ids=[str(bi_id)],
            user_class="undergraduate",
            locale="ko",
        )

        # status='completed' 검증.
        status_val = await redis_client.hget(
            RedisKey.cold_start_status(uuid.UUID(request_id)), "status"
        )
        assert status_val == "completed"
        dashboard_ready = await redis_client.hget(
            RedisKey.cold_start_status(uuid.UUID(request_id)), "dashboard_ready"
        )
        assert dashboard_ready == "true"

        # Recommendation rows 검증.
        async with session_factory() as verify_session:
            recs = (
                await verify_session.execute(
                    select(Recommendation).where(
                        Recommendation.user_id == user_id
                    )
                )
            ).scalars().all()
            assert len(recs) == 10
            slot_counts: dict[str, int] = {}
            for r in recs:
                slot_counts[r.slot_type] = slot_counts.get(r.slot_type, 0) + 1
            assert slot_counts.get("core") == 5
            assert slot_counts.get("adjacent") == 3
            assert slot_counts.get("discovery") == 2

            # pseudo Document 10개 (content_type='pseudo_cold_start')
            pseudo_docs = (
                await verify_session.execute(
                    select(Document).where(
                        Document.content_type == "pseudo_cold_start",
                        Document.source_id
                        == rec_cold_start_sentinel.source_id,
                    )
                )
            ).scalars().all()
            assert len(pseudo_docs) >= 10

            # RecommendationSlot 3개 (target_count 5/3/2).
            slots = (
                await verify_session.execute(
                    select(RecommendationSlot).where(
                        RecommendationSlot.user_id == user_id
                    )
                )
            ).scalars().all()
            assert len(slots) == 3
    finally:
        # cleanup — test DB 격리.
        async with session_factory() as cleanup:
            await cleanup.execute(
                Recommendation.__table__.delete().where(
                    Recommendation.user_id == user_id
                )
            )
            await cleanup.execute(
                RecommendationSlot.__table__.delete().where(
                    RecommendationSlot.user_id == user_id
                )
            )
            await cleanup.execute(
                Document.__table__.delete().where(
                    Document.content_type == "pseudo_cold_start",
                    Document.source_id == rec_cold_start_sentinel.source_id,
                )
            )
            await cleanup.execute(
                BroadInterest.__table__.delete().where(
                    BroadInterest.broad_interest_id == bi_id
                )
            )
            from app.db.models import User as UserModel
            await cleanup.execute(
                UserModel.__table__.delete().where(
                    UserModel.user_id == user_id
                )
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_run_cold_start_provider_error_sets_failed(
    db_engine,
    redis_client: aioredis.Redis,
    rec_user,
    rec_cso_topics,
    rec_cold_start_sentinel,
) -> None:
    """ProviderError 시 status='failed' + error_code='cold_start.llm_failed'."""
    session_factory = async_sessionmaker(bind=db_engine)
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(rec_user.__class__).where(
                    rec_user.__class__.user_id == rec_user.user_id
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(rec_user)
        bi = BroadInterest(
            broad_interest_id=uuid.uuid4(),
            name=f"test-bi-{uuid.uuid4().hex[:6]}",
            description="x",
            cso_cluster_label="AI",
            cso_seed_topic_id=rec_cso_topics[0].cso_topic_id,
        )
        session.add(bi)
        await session.commit()
        bi_id = bi.broad_interest_id
        user_id = rec_user.user_id

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=ProviderError("simulated"))
    settings = get_settings()
    request_id = str(uuid.uuid4())

    try:
        await run_cold_start(
            session_factory,
            redis_client,
            provider,
            settings,
            request_id=request_id,
            user_id=str(user_id),
            cluster_ids=[str(bi_id)],
            user_class="undergraduate",
            locale="ko",
        )

        status_val = await redis_client.hget(
            RedisKey.cold_start_status(uuid.UUID(request_id)), "status"
        )
        assert status_val == "failed"
        error_code = await redis_client.hget(
            RedisKey.cold_start_status(uuid.UUID(request_id)), "error_code"
        )
        assert error_code == "cold_start.llm_failed"
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(
                BroadInterest.__table__.delete().where(
                    BroadInterest.broad_interest_id == bi_id
                )
            )
            from app.db.models import User as UserModel
            await cleanup.execute(
                UserModel.__table__.delete().where(
                    UserModel.user_id == user_id
                )
            )
            await cleanup.commit()
