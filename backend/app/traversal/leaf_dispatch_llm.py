"""C-57 (2026-05-24) retract_reposition + split_dispatch LLM 호출.

명세: `docs/algorithms/leaf-topic-lifecycle.md §Retract 시 LLM 프롬프트` +
`docs/algorithms/cso-topic-traversal.md §3.3 split`.

직전까지 `default.evaluate_retract` / `evaluate_split` 가 1차 시연 stub 사용:
- retract: 모두 new_path[-1] 로 remap fallback (archive 결정 없음)
- split: 모두 source.child_A 한쪽으로 (new T' 는 leaf 0개)

본 모듈: LLM (high slot) 으로 leaf 별 dispatch 결정. 실패 시 caller 가 stub fallback
유지 (fail-safe). 사용자 결정 매트릭스 (decisions.md §20):
- retract decision = 2종 (remap to new_path[-1] | archive)
- split decision = 2종 (target_trace = source | new) — archive 결정 제외
- 호출 시점 = inline (별도 worker job 아님)
- model_slot = high (identify_emerging 답습)

응답 anchor 검증:
- retract: new_cso_topic_id ∈ new_path (또는 archive)
- split: target_cso_topic_id ∈ {source_child, new_child}
- 위반 candidate 는 stub fallback 처리 (caller 가 조용히 default 사용)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.llm_provider.protocol import (
    ChatMessage,
    FixtureNotFound,
    LLMProvider,
    ProviderError,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class LeafSummary:
    """LLM prompt context — leaf 1건."""

    leaf_id: UUID
    label_ko: str


# ============================================================
# retract_reposition
# ============================================================


SYSTEM_PROMPT_RETRACT = """당신은 학술/기술 토픽 큐레이션 어시스턴트다.

사용자의 trace path 가 단축됐다 (retract). retract 된 토픽 산하의 dynamic leaf 들이
새 path 끝 토픽 차원에서도 의미가 있는지 판단하라.

[지시]
- 각 leaf 에 대해 두 결정 중 하나:
  - "remap": leaf 가 새 path 끝 토픽 차원에서도 의미 있음 — 그 cso_topic 으로 재매핑
  - "archive": leaf 가 retract 된 토픽 specific 해서 새 path 차원에서는 의미 잃음
- 응답은 JSON: {"decisions": [{"leaf_id": "<UUID>", "decision": "remap"|"archive"}]}.
- 빈 응답 가능 (모든 leaf archive 라면 모두 "archive" 명시).
"""


def _build_retract_user_prompt(
    *,
    retracted_label: str,
    new_path_labels: list[str],
    leaves: list[LeafSummary],
) -> str:
    parts: list[str] = [
        f"[retract 된 토픽] {retracted_label}",
        f"[새 trace path] {' → '.join(new_path_labels)}",
        f"[retract 된 토픽 산하 leaf ({len(leaves)})]",
    ]
    for lf in leaves:
        parts.append(f"- id={lf.leaf_id} | {lf.label_ko}")
    return "\n".join(parts)


async def call_retract_reposition(
    provider: LLMProvider,
    *,
    user_id: UUID,
    retracted_label: str,
    new_path_labels: list[str],
    new_path_tail_cso: UUID,
    leaves: list[LeafSummary],
) -> list[dict[str, Any]] | None:
    """LLM 호출 → decisions list 반환. 실패 시 None (caller stub fallback).

    응답 decision="remap" 시 new_cso_topic_id = new_path_tail_cso 강제 (단순화 default —
    명세의 "path 중간 노드 remap" 은 본 라운드 scope 밖).
    """
    if not leaves:
        return []
    user_content = _build_retract_user_prompt(
        retracted_label=retracted_label,
        new_path_labels=new_path_labels,
        leaves=leaves,
    )
    try:
        response = await provider.complete(
            messages=[
                ChatMessage(role="system", content=SYSTEM_PROMPT_RETRACT),
                ChatMessage(role="user", content=user_content),
            ],
            model_slot="high",
            response_format="json",
            user_id=str(user_id),
        )
    except (FixtureNotFound, ProviderError) as exc:
        logger.warning(
            "retract_reposition LLM unavailable user=%s err=%s — stub fallback",
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
                "retract_reposition JSON parse fail user=%s — stub fallback", user_id
            )
            return None
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("decisions", [])
    if not isinstance(raw, list):
        return None

    # leaf_id → input UUID set (응답 hallucination 차단).
    valid_ids = {lf.leaf_id for lf in leaves}
    decisions: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            leaf_id = UUID(str(item.get("leaf_id", "")))
        except (ValueError, TypeError):
            continue
        if leaf_id not in valid_ids:
            continue
        action = str(item.get("decision", "")).lower()
        if action not in ("remap", "archive"):
            continue
        decisions.append(
            {
                "leaf_id": leaf_id,
                "decision": action,
                "new_cso_topic_id": new_path_tail_cso if action == "remap" else None,
            }
        )
    return decisions


# ============================================================
# split_dispatch
# ============================================================


SYSTEM_PROMPT_SPLIT = """당신은 학술/기술 토픽 큐레이션 어시스턴트다.

사용자의 trace 가 분기점에서 두 자식 토픽으로 split 됐다. 분기점 산하의 dynamic
leaf 들이 두 자식 중 어느 쪽 의미가 더 강한지 판단하라.

[지시]
- 각 leaf 에 대해 target_trace 중 하나 선택:
  - "source": child_A 차원에서 의미 강함 (T 가 child_A 방향)
  - "new": child_B 차원에서 의미 강함 (T' 가 child_B 방향)
- 응답은 JSON: {"decisions": [{"leaf_id": "<UUID>", "target_trace": "source"|"new"}]}.
- 양쪽 모두 의미 있어도 더 강한 쪽 선택 (애매하면 "source" 권장).
"""


def _build_split_user_prompt(
    *,
    fork_label: str,
    source_child_label: str,
    new_child_label: str,
    leaves: list[LeafSummary],
) -> str:
    parts: list[str] = [
        f"[분기점] {fork_label}",
        f"[source trace T (child_A)] {source_child_label}",
        f"[new trace T' (child_B)] {new_child_label}",
        f"[분기점 산하 leaf ({len(leaves)})]",
    ]
    for lf in leaves:
        parts.append(f"- id={lf.leaf_id} | {lf.label_ko}")
    return "\n".join(parts)


async def call_split_dispatch(
    provider: LLMProvider,
    *,
    user_id: UUID,
    fork_label: str,
    source_child_label: str,
    new_child_label: str,
    source_child_cso: UUID,
    new_child_cso: UUID,
    leaves: list[LeafSummary],
) -> list[dict[str, Any]] | None:
    """LLM 호출 → decisions list 반환. 실패 시 None (caller stub fallback).

    target_trace = "source" → target_cso_topic_id = source_child_cso
    target_trace = "new" → target_cso_topic_id = new_child_cso
    """
    if not leaves:
        return []
    user_content = _build_split_user_prompt(
        fork_label=fork_label,
        source_child_label=source_child_label,
        new_child_label=new_child_label,
        leaves=leaves,
    )
    try:
        response = await provider.complete(
            messages=[
                ChatMessage(role="system", content=SYSTEM_PROMPT_SPLIT),
                ChatMessage(role="user", content=user_content),
            ],
            model_slot="high",
            response_format="json",
            user_id=str(user_id),
        )
    except (FixtureNotFound, ProviderError) as exc:
        logger.warning(
            "split_dispatch LLM unavailable user=%s err=%s — stub fallback",
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
                "split_dispatch JSON parse fail user=%s — stub fallback", user_id
            )
            return None
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("decisions", [])
    if not isinstance(raw, list):
        return None

    valid_ids = {lf.leaf_id for lf in leaves}
    decisions: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            leaf_id = UUID(str(item.get("leaf_id", "")))
        except (ValueError, TypeError):
            continue
        if leaf_id not in valid_ids:
            continue
        target = str(item.get("target_trace", "")).lower()
        if target == "source":
            target_cso = source_child_cso
        elif target == "new":
            target_cso = new_child_cso
        else:
            continue
        decisions.append(
            {
                "leaf_id": leaf_id,
                "target_trace": target,
                "target_cso_topic_id": target_cso,
            }
        )
    return decisions


__all__ = [
    "LeafSummary",
    "SYSTEM_PROMPT_RETRACT",
    "SYSTEM_PROMPT_SPLIT",
    "call_retract_reposition",
    "call_split_dispatch",
]
