"""추천 이유 (reason_short) — LLM medium 1회 batch 호출.

recommendation-ranking.md §추천 이유. 한국어 1문장 ≤80자. NFR-03/NFR-04 정합.

응답 검증:
- 길이 ≤80자
- 거부 키워드 ("bucket", "score", "점수", "알고리즘", "weight", "ranking") 포함 시 룰 fallback

LLM 실패 / 누락 카드 / 검증 실패 → 룰 fallback: "토픽: {leaf_label or cso_label} · 출처: {source_name}".
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from uuid import UUID

from app.llm_provider.protocol import (
    ChatMessage,
    LLMBudgetExceeded,
    LLMProvider,
    ProviderError,
)

from .ranking import ScoredCandidate

logger = logging.getLogger(__name__)

# 거부 키워드 — 점수·알고리즘 노출 차단 (NFR-04).
_REJECTED_KEYWORDS = (
    "bucket",
    "score",
    "점수",
    "알고리즘",
    "weight",
    "ranking",
    "랭킹",
    "확률",
)
_MAX_REASON_LENGTH = 80


def _rule_fallback(card: ScoredCandidate) -> str:
    """룰 기반 reason — LLM 실패 시 사용. ≤80자 보장."""
    topic_label = card.leaf_label or card.cso_label or "관심 토픽"
    src = card.source_name or "출처"
    text = f"{topic_label} 관련 · {src}"
    if len(text) > _MAX_REASON_LENGTH:
        text = text[: _MAX_REASON_LENGTH - 1] + "…"
    return text


def _is_valid_reason(text: str) -> bool:
    """≤80자 + 거부 키워드 부재."""
    if not text or len(text) > _MAX_REASON_LENGTH:
        return False
    lower = text.lower()
    for kw in _REJECTED_KEYWORDS:
        if kw in lower:
            return False
    return True


def _build_messages(cards: list[ScoredCandidate]) -> list[ChatMessage]:
    """LLM batch prompt — JSON list 입력 · JSON list 응답.

    응답 형식:
    {
      "reasons": [
        {"document_id": "<uuid>", "reason_short_ko": "<≤80자 한국어 1문장>"},
        ...
      ]
    }
    """
    system = (
        "당신은 사용자에게 추천 이유를 한국어 한 문장으로 설명하는 어시스턴트다. "
        "점수, 모델, 알고리즘, 랭킹, 확률 같은 시스템 용어는 절대 언급하지 않는다. "
        "토픽 라벨과 출처만 자연어로 활용하여 80자 이내로 작성한다."
    )
    payload = {
        "cards": [
            {
                "document_id": str(c.document_id),
                "topic_label": c.leaf_label or c.cso_label or "관심 토픽",
                "source_name": c.source_name,
                "slot_hint": (
                    "current"
                    if c.cso_topic_id is not None and c.leaf_topic_id is not None
                    else "topic"
                ),
            }
            for c in cards
        ],
    }
    user = (
        "다음 카드 각각에 대해 한국어 한 문장 추천 이유를 작성하라. JSON 으로만 응답한다.\n"
        "응답 형식: {\"reasons\": [{\"document_id\": \"...\", \"reason_short_ko\": \"...\"}]}\n\n"
        f"카드 목록:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]


async def generate_reasons(
    provider: LLMProvider,
    cards: Iterable[ScoredCandidate],
    *,
    user_id: UUID | None = None,
) -> dict[UUID, str]:
    """LLM 1회 batch — `dict[document_id, reason_short]` 반환.

    LLM 실패 / 카드 누락 / 검증 실패 → 룰 fallback 으로 보충.
    반드시 입력 카드 모두 dict 에 포함됨.
    """
    card_list = list(cards)
    if not card_list:
        return {}
    result: dict[UUID, str] = {}
    try:
        resp = await provider.complete(
            _build_messages(card_list),
            model_slot="medium",
            response_format="json",
            user_id=str(user_id) if user_id else None,
        )
        parsed = resp.parsed_json
        if isinstance(parsed, dict):
            reasons_raw = parsed.get("reasons", [])
            if isinstance(reasons_raw, list):
                for item in reasons_raw:
                    if not isinstance(item, dict):
                        continue
                    doc_id_raw = item.get("document_id")
                    reason_raw = item.get("reason_short_ko")
                    if not isinstance(doc_id_raw, str) or not isinstance(
                        reason_raw, str
                    ):
                        continue
                    try:
                        doc_id = UUID(doc_id_raw)
                    except (ValueError, TypeError):
                        continue
                    cleaned = reason_raw.strip()
                    if _is_valid_reason(cleaned):
                        result[doc_id] = cleaned
    except (ProviderError, LLMBudgetExceeded, json.JSONDecodeError) as exc:
        logger.warning("generate_reasons LLM failed; rule fallback: %s", exc)
    except Exception as exc:
        logger.warning(
            "generate_reasons unexpected failure; rule fallback: %s: %s",
            type(exc).__name__,
            exc,
        )
    # 누락된 카드는 룰 fallback.
    for c in card_list:
        if c.document_id not in result:
            result[c.document_id] = _rule_fallback(c)
    return result


__all__ = ["generate_reasons"]
