"""reason_short LLM batch — 길이/키워드 검증 + 룰 fallback."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.llm_provider.protocol import LLMResponse, ProviderError
from app.recommendation.ranking import ScoredCandidate
from app.recommendation.reasons import generate_reasons


def _card(*, leaf_label: str | None = None, cso_label: str = "AI") -> ScoredCandidate:
    return ScoredCandidate(
        document_id=uuid.uuid4(),
        title="t",
        source_id=uuid.uuid4(),
        source_name="arXiv",
        source_type="academic",
        trust_level="high",
        published_at=datetime.now(UTC),
        cso_topic_id=uuid.uuid4(),
        leaf_topic_id=uuid.uuid4() if leaf_label else None,
        leaf_status="active" if leaf_label else None,
        leaf_label=leaf_label,
        cso_label=cso_label,
        topic_confidence=0.9,
        topic_match=0.8,
        freshness=1.0,
        trust=1.0,
        score=0.85,
    )


def _make_response(parsed: object) -> LLMResponse:
    return LLMResponse(
        text=json.dumps(parsed, ensure_ascii=False),
        model="mock",
        prompt_tokens=10,
        completion_tokens=20,
        parsed_json=parsed,
    )


@pytest.mark.asyncio
async def test_valid_korean_reason_under_80_chars() -> None:
    card = _card(cso_label="컴퓨터비전")
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=_make_response(
            {
                "reasons": [
                    {
                        "document_id": str(card.document_id),
                        "reason_short_ko": "컴퓨터비전 신규 발표 자료",
                    }
                ]
            }
        )
    )
    reasons = await generate_reasons(provider, [card])
    assert card.document_id in reasons
    assert reasons[card.document_id] == "컴퓨터비전 신규 발표 자료"
    assert len(reasons[card.document_id]) <= 80


@pytest.mark.asyncio
async def test_rejects_score_keyword() -> None:
    """'score' / 'bucket' / '점수' 포함 시 룰 fallback."""
    card = _card(cso_label="NLP")
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=_make_response(
            {
                "reasons": [
                    {
                        "document_id": str(card.document_id),
                        "reason_short_ko": "당신의 점수가 높아 추천합니다",
                    }
                ]
            }
        )
    )
    reasons = await generate_reasons(provider, [card])
    # 거부 → 룰 fallback (관심 토픽 + 출처).
    assert card.document_id in reasons
    assert "점수" not in reasons[card.document_id]
    assert "NLP" in reasons[card.document_id]


@pytest.mark.asyncio
async def test_provider_error_falls_back_to_rule() -> None:
    """LLM ProviderError 시 모든 카드 룰 fallback."""
    cards = [_card(cso_label="RAG"), _card(leaf_label="LLM-fine-tuning")]
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=ProviderError("network fail"))
    reasons = await generate_reasons(provider, cards)
    assert len(reasons) == 2
    # 룰 fallback — leaf_label 우선, 없으면 cso_label.
    assert "RAG" in reasons[cards[0].document_id]
    assert "LLM-fine-tuning" in reasons[cards[1].document_id]


@pytest.mark.asyncio
async def test_missing_card_response_uses_rule() -> None:
    """LLM 응답에 누락된 카드는 룰 fallback 으로 보충."""
    cards = [_card(cso_label="A"), _card(cso_label="B")]
    provider = AsyncMock()
    # 응답에 cards[0] 만.
    provider.complete = AsyncMock(
        return_value=_make_response(
            {
                "reasons": [
                    {
                        "document_id": str(cards[0].document_id),
                        "reason_short_ko": "A 토픽 자료",
                    }
                ]
            }
        )
    )
    reasons = await generate_reasons(provider, cards)
    assert len(reasons) == 2
    assert reasons[cards[0].document_id] == "A 토픽 자료"
    # cards[1] 은 룰 fallback.
    assert "B" in reasons[cards[1].document_id]


@pytest.mark.asyncio
async def test_truncates_over_80_chars() -> None:
    """LLM 이 80자 초과해도 룰 fallback 으로 처리 (검증 거부)."""
    card = _card(cso_label="X")
    provider = AsyncMock()
    long_text = "가" * 100
    provider.complete = AsyncMock(
        return_value=_make_response(
            {
                "reasons": [
                    {
                        "document_id": str(card.document_id),
                        "reason_short_ko": long_text,
                    }
                ]
            }
        )
    )
    reasons = await generate_reasons(provider, [card])
    # 80자 초과 응답 거부 → 룰 fallback.
    assert len(reasons[card.document_id]) <= 80
    assert "X" in reasons[card.document_id]
