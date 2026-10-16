"""C-73 (2026-06-11) Fusion bridge LLM 선택 — 닫힌 후보 목록에서 선택 또는 거부.

원 설계 의도 복원: 그래프(`fusion_bridge.find_fusion_bridge_candidates`)가 두 trace
사이의 bridge 후보를 결정론적으로 생성하고, LLM 은 그 **닫힌 목록 안에서만** 의미
판단으로 1개를 고르거나 명시적으로 거부한다.

거부가 일급 출력인 이유 (실측 근거): cross-cluster trace 쌍의 ~48% 는 깊이 필터
통과 후보가 빈약 — 강제 선택 시 "허브를 LLM 권위로 세탁한 답" 이 나온다. 거부 시
caller 가 fusion_candidates=[] → trend fallback (기존 경로) 처리.

가드 (leaf_dispatch_llm 패턴 답습):
- 선택 ID ∉ 후보 풀 → 거부 취급 (hallucination 차단)
- FixtureNotFound / ProviderError / JSON parse 실패 → 거부 취급
  (CI mock 환경에서 fusion 빈 풀 → trend fallback, 안전)

model_slot = "medium" — 닫힌 목록 선택은 생성 과제가 아니므로 high 불필요
(자체 결정, decisions.md §32).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from uuid import UUID

from app.llm_provider.protocol import (
    ChatMessage,
    FixtureNotFound,
    LLMProvider,
    ProviderError,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class BridgeOption:
    """LLM prompt context — bridge 후보 1건."""

    cso_topic_id: UUID
    label: str


@dataclass(slots=True, frozen=True)
class BridgeSelection:
    """LLM 선택 결과 — 선택된 후보 + 이유."""

    cso_topic_id: UUID
    reasoning: str


SYSTEM_PROMPT_FUSION_SELECT = """당신은 학술/기술 토픽 큐레이션 어시스턴트다.

사용자의 과거 관심 영역(archived trace)과 현재 관심 영역(active trace)이 주어진다.
두 영역의 의미적 교차점(bridge)으로 가장 적합한 토픽을 후보 목록에서 골라라.
bridge 는 "두 영역의 지식이 실제로 만나 새 학습 방향이 되는" 토픽이다 — 예:
과거 Graph Algorithms x 현재 Memory Management 의 bridge 는 Memory-bounded
Algorithms 같은 것.

[지시]
- 후보는 모두 그래프 알고리즘이 두 영역 사이에서 찾은 노드다. 그중 두 영역의
  교차로서 의미가 가장 강한 토픽 하나를 선택하라.
- 어느 후보도 의미 있는 교차가 아니라고 판단하면 거부하라 — 억지 조합을
  고르는 것보다 거부가 옳다. 거부 시 bridge_cso_topic_id 를 null 로.
- 응답은 JSON 하나:
  {"bridge_cso_topic_id": "<후보의 UUID>" 또는 null,
   "reasoning": "<선택/거부 이유, 한국어 20~300자>"}
"""


def _build_user_prompt(
    *,
    archived_path_labels: list[str],
    active_path_labels: list[str],
    options: list[BridgeOption],
) -> str:
    parts: list[str] = [
        f"[과거 관심 (archived trace)] {' → '.join(archived_path_labels)}",
        f"[현재 관심 (active trace)] {' → '.join(active_path_labels)}",
        f"[bridge 후보 ({len(options)})]",
    ]
    for opt in options:
        parts.append(f"- id={opt.cso_topic_id} | {opt.label}")
    return "\n".join(parts)


async def call_fusion_bridge_select(
    provider: LLMProvider,
    *,
    user_id: UUID,
    archived_path_labels: list[str],
    active_path_labels: list[str],
    options: list[BridgeOption],
) -> BridgeSelection | None:
    """LLM 호출 → 후보 중 선택 결과 반환. None = 거부 또는 실패 (caller fallback).

    None 분기 (모두 fusion_candidates=[] → trend fallback 로 수렴):
    - LLM 명시 거부 (bridge_cso_topic_id=null)
    - 선택 ID 가 후보 풀 밖 (hallucination)
    - provider 실패 / JSON parse 실패
    """
    if not options:
        return None
    user_content = _build_user_prompt(
        archived_path_labels=archived_path_labels,
        active_path_labels=active_path_labels,
        options=options,
    )
    try:
        response = await provider.complete(
            messages=[
                ChatMessage(role="system", content=SYSTEM_PROMPT_FUSION_SELECT),
                ChatMessage(role="user", content=user_content),
            ],
            model_slot="medium",
            response_format="json",
            user_id=str(user_id),
        )
    except (FixtureNotFound, ProviderError) as exc:
        logger.warning(
            "fusion_bridge_select LLM unavailable user=%s err=%s — refusal fallback",
            user_id,
            exc,
        )
        return None

    parsed = response.parsed_json
    if parsed is None and response.text:
        try:
            parsed = json.loads(response.text)
        except (ValueError, json.JSONDecodeError):
            logger.warning(
                "fusion_bridge_select JSON parse fail user=%s — refusal fallback",
                user_id,
            )
            return None
    if not isinstance(parsed, dict):
        return None

    raw_id = parsed.get("bridge_cso_topic_id")
    if raw_id is None:
        logger.info("fusion_bridge_select LLM refused user=%s", user_id)
        return None
    try:
        chosen = UUID(str(raw_id))
    except (ValueError, TypeError):
        logger.warning(
            "fusion_bridge_select invalid uuid user=%s raw=%r — refusal fallback",
            user_id,
            raw_id,
        )
        return None

    valid_ids = {opt.cso_topic_id for opt in options}
    if chosen not in valid_ids:
        logger.warning(
            "fusion_bridge_select out-of-pool id user=%s chosen=%s — refusal fallback",
            user_id,
            chosen,
        )
        return None

    reasoning = str(parsed.get("reasoning", "")).strip()[:300]
    return BridgeSelection(cso_topic_id=chosen, reasoning=reasoning)


__all__ = [
    "SYSTEM_PROMPT_FUSION_SELECT",
    "BridgeOption",
    "BridgeSelection",
    "call_fusion_bridge_select",
]
