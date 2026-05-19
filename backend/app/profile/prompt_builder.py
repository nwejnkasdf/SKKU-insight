"""LLM prompt SOR — daily user_profile cron 의 system + user prompt 조립.

핵심 룰 (system_prompt):
1. NFR-04 정합 — 점수·알고리즘·랭킹·확률·버킷 키워드 금지.
2. CSO 노드 ID 매핑 강제 — `bridge_cso_topic_id` / seeds 의 `cso_topic_id` 는
   `cso_candidate_pool` 안의 ID 만 사용. 입력에 없는 UUID 생성 금지.
3. JSON 스키마 정확히 따름 (additionalProperties=False, 모든 필드 required).
4. 한국어 응답 (자유 텍스트 3 필드는 1-2 문장 한국어).
5. 시스템 정체성 — "두 영역이 만나는 새 학습 path 발굴" (archive x active fusion).

`decisions.md §15` 결정 매트릭스 + `algorithms/recommendation-ranking.md §Discovery`.
"""
from __future__ import annotations

import json
from typing import Any

from app.profile.schemas import (
    USER_PROFILE_JSON_SCHEMA,
    ProfileLLMInput,
)


def build_system_prompt(generator_version: str) -> str:
    """한국어 system prompt. generator_version 으로 prompt 변경 추적.

    변경 시 `USER_PROFILE_GENERATOR_VERSION` env bump → UserProfile.generator_version
    컬럼 값 갱신 → daily cron 매일 재생성으로 자연 교체.
    """
    return (
        f"당신은 사용자의 흥미 궤적을 분석해 '교차점에서 새 방향성을 발굴' 하는 어시스턴트다. "
        f"응답은 반드시 한국어로 작성하며, 점수·알고리즘·확률·랭킹·버킷·신뢰도 같은 시스템 "
        f"용어는 절대 언급하지 않는다.\n\n"
        f"입력 데이터:\n"
        f"  - active_traces: 사용자의 현재 활성 관심 궤적 (path label chain).\n"
        f"  - archived_traces: 과거 강한 흥미로 종료된 보관 궤적 (충분히 큰 흥미 신호만 사전 필터).\n"
        f"  - top_interest_states: 최근 관심 강도 상위 토픽.\n"
        f"  - recent_saved/hidden/not_interested_topic_labels: 명시 피드백 신호.\n"
        f"  - cso_candidate_pool: bridge / seed 로 사용 가능한 CSO 토픽 ID 풀.\n\n"
        f"당신의 작업 (6 필드 응답):\n"
        f"  A. recent_signals_summary: 최근 active day 동안의 핵심 흥미 변화를 1-2 문장으로 요약. "
        f"     ≤ 400자. 점수·확률 등 시스템 용어 금지.\n"
        f"  B. persistent_tendencies_summary: archive 와 active 양쪽에 반복 등장하는 지속 성향 "
        f"     1-2 문장. ≤ 400자.\n"
        f"  C. likely_dislikes_summary: 명시 hide / not_interested 패턴에서 추론한 비흥미 영역 "
        f"     1-2 문장. ≤ 400자.\n"
        f"  D. fusion_candidates (0-3개): archived 영역과 active 영역의 *교차점* 에서 새 방향을 발굴.\n"
        f"     각 candidate 는 from_archived (archive 라벨 1-3개) + from_active (active 라벨 1-3개) + "
        f"     bridge_label (두 영역을 잇는 새 영역의 한국어 라벨) + bridge_cso_topic_id (반드시 "
        f"     cso_candidate_pool 안에서 선택) + bridge_reasoning (왜 두 영역이 만나는지 1-2 "
        f"     문장 한국어 설명, 20-300자). 두 영역이 너무 멀어 무의미한 bridge 라면 candidate 0개.\n"
        f"  E. deepening_seeds (0-3개): active 영역 안의 미답방 후속 영역 — cso_candidate_pool 에서 선택. "
        f"     {{cso_topic_id, label}}.\n"
        f"  F. broadening_seeds (0-3개): active 영역 외 다른 cluster 중 사용자 지속 성향과 친화도가 "
        f"     높은 영역 — cso_candidate_pool 에서 선택. {{cso_topic_id, label}}.\n\n"
        f"CSO 노드 ID 매핑 강제: bridge_cso_topic_id / cso_topic_id 는 모두 입력 cso_candidate_pool "
        f"안의 UUID 만 사용. 입력에 없는 새 UUID 생성 시 응답이 거부된다.\n\n"
        f"응답은 다음 JSON 스키마를 *정확히* 따른다 (additionalProperties=false, 모든 properties 가 "
        f"required, 자유 텍스트 length 제한 준수).\n\n"
        f"generator_version={generator_version}."
    )


def build_user_prompt(input_payload: dict[str, Any]) -> str:
    """user prompt — input 데이터 JSON 직렬화 + output schema echo.

    output schema 를 user prompt 에도 echo: codex/openai 모두 `--output-schema` /
    `response_format` 으로 강제 적용하지만, prompt 안에 schema 한 번 더 노출하면 LLM 이
    자연스럽게 형식 인지 (특히 fallback path 안전).
    """
    return (
        "입력 데이터:\n"
        f"{json.dumps(input_payload, ensure_ascii=False, indent=2)}\n\n"
        "응답 형식 (JSON):\n"
        f"{json.dumps(USER_PROFILE_JSON_SCHEMA, ensure_ascii=False)}"
    )


def to_input_payload(llm_input: ProfileLLMInput) -> dict[str, Any]:
    """ProfileLLMInput → LLM prompt 에 들어갈 dict 직렬화 (UUID → str)."""
    return llm_input.model_dump(mode="json")


def estimate_prompt_tokens(payload: dict[str, Any]) -> int:
    """LLM 호출 전 토큰 budget 추정 (char/4 휴리스틱).

    실 tokenizer (tiktoken 등) 미사용 — 의존성 회피. char/4 는 영문/한국어 mix 에서
    ±30% 정확도이나 LLM_DAILY_TOKEN_BUDGET 검사에 충분.
    """
    serialized = json.dumps(payload, ensure_ascii=False)
    return len(serialized) // 4


__all__ = [
    "build_system_prompt",
    "build_user_prompt",
    "estimate_prompt_tokens",
    "to_input_payload",
]
