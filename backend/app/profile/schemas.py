"""Pydantic schemas + JSONSchema spec — LLM `--output-schema` / `response_format` 강제용.

LLM (codex_oauth / openai) 응답은 `UserProfilePayload` 구조를 *정확히* 따라야 한다.
codex 의 `--output-schema` 와 openai 의 `response_format={type:"json_schema", strict:true}`
양쪽이 동일한 schema spec 을 사용 — `USER_PROFILE_JSON_SCHEMA` 가 SOR.

`additionalProperties=False` + 모든 필드 `required` 강제 — codex 의 strict 모드 요건.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FusionCandidate(BaseModel):
    """archive x current cross-product 융합 영역. discovery slot 1 (Fusion) 의 후보."""

    model_config = ConfigDict(extra="forbid")

    from_archived: list[str] = Field(min_length=1, max_length=3)
    from_active: list[str] = Field(min_length=1, max_length=3)
    bridge_label: str = Field(min_length=2, max_length=80)
    bridge_cso_topic_id: UUID
    bridge_reasoning: str = Field(min_length=20, max_length=300)


class TopicSeed(BaseModel):
    """deepening_seeds / broadening_seeds 의 단일 entry. fallback 용 CSO 토픽."""

    model_config = ConfigDict(extra="forbid")

    cso_topic_id: UUID
    label: str = Field(min_length=1, max_length=80)


class UserProfilePayload(BaseModel):
    """LLM 응답 payload — daily cron 이 받아 UserProfile 테이블에 영속.

    6 필드 구조화 (3 텍스트 + 3 JSONB array). 자유 텍스트는 한국어 1-2 문장 강제
    (LLM prompt instruction + max_length).
    """

    model_config = ConfigDict(extra="forbid")

    recent_signals_summary: str = Field(max_length=400)
    persistent_tendencies_summary: str = Field(max_length=400)
    likely_dislikes_summary: str = Field(max_length=400)
    fusion_candidates: list[FusionCandidate] = Field(default_factory=list, max_length=3)
    deepening_seeds: list[TopicSeed] = Field(default_factory=list, max_length=3)
    broadening_seeds: list[TopicSeed] = Field(default_factory=list, max_length=3)


class ActiveTraceSummary(BaseModel):
    """LLM input — active trace 1건의 요약 (path label chain + score_tail)."""

    model_config = ConfigDict(extra="forbid")

    trace_id: UUID
    path_labels: list[str]
    path_cso_topic_ids: list[UUID]
    score_tail: float
    last_activity_active_day: int


class ArchivedTraceSummary(BaseModel):
    """LLM input — archived trace 1건의 요약 (`score_tail >= 0.6` 만 필터)."""

    model_config = ConfigDict(extra="forbid")

    trace_id: UUID
    path_labels: list[str]
    path_cso_topic_ids: list[UUID]
    score_tail_at_archive: float
    last_activity_active_day: int
    archived_at_active_day: int


class InterestStateSummary(BaseModel):
    """LLM input — UserInterestState 상위 row 의 자연어 요약."""

    model_config = ConfigDict(extra="forbid")

    cso_topic_id: UUID | None
    leaf_topic_id: UUID | None
    label: str
    long_score: float
    short_score: float


class CSOTopicCandidate(BaseModel):
    """LLM input — cso_candidate_pool entry. LLM 이 bridge_cso_topic_id 선택 풀."""

    model_config = ConfigDict(extra="forbid")

    cso_topic_id: UUID
    label: str


class CSOTopicCandidatePool(BaseModel):
    """LLM input — 카테고리별 후보 풀 (C-44 P2-28, 2026-05-19).

    fix 전: 단일 union list — LLM 이 fusion bridge 로 deepening 풀 ID 선택해도 검증 통과.
    fix 후: 카테고리별 분리 — fusion / deepening / broadening 각각 다른 의미의 풀.
    응답 검증 (`generate_profile_payload`) 이 각 카테고리 ID 가 자기 풀 안에 있는지 확인.

    - fusion: archive + active path 안 + active tail 1-hop 이웃 (교차점 후보)
    - deepening: active path 안 + active tail 1-hop successors (미답방 자손)
    - broadening: archived path 안 + active 외 cluster root (다른 영역)
    """

    model_config = ConfigDict(extra="forbid")

    fusion: list[CSOTopicCandidate]
    deepening: list[CSOTopicCandidate]
    broadening: list[CSOTopicCandidate]


class ProfileLLMInput(BaseModel):
    """daily cron 이 fetch 한 LLM 입력 데이터 묶음. prompt_builder 가 dict 직렬화."""

    model_config = ConfigDict(extra="forbid")

    user_active_day_counter: int
    active_traces: list[ActiveTraceSummary]
    archived_traces: list[ArchivedTraceSummary]
    top_interest_states: list[InterestStateSummary]
    recent_saved_topic_labels: list[str]
    recent_hidden_topic_labels: list[str]
    not_interested_topic_labels: list[str]
    # (C-44 P2-28) 카테고리별 풀 — fusion / deepening / broadening 키.
    cso_candidate_pool: CSOTopicCandidatePool


def _build_user_profile_json_schema() -> dict[str, Any]:
    """`UserProfilePayload.model_json_schema()` 변환 + codex strict 호환 post-process.

    codex `--output-schema` 요건:
    - 모든 객체에 `additionalProperties: false`
    - 모든 properties 가 `required` list 에 포함
    - `$defs` 또는 `definitions` reference 펼침 (codex 가 ref 미해석 시 대비)
    """
    # Pydantic v2 의 자동 생성 schema 를 펼친 형태로 정의 — codex strict 호환성 우선.
    seed_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["cso_topic_id", "label"],
        "properties": {
            "cso_topic_id": {"type": "string", "format": "uuid"},
            "label": {"type": "string", "minLength": 1, "maxLength": 80},
        },
    }
    fusion_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "from_archived",
            "from_active",
            "bridge_label",
            "bridge_cso_topic_id",
            "bridge_reasoning",
        ],
        "properties": {
            "from_archived": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "from_active": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "bridge_label": {"type": "string", "minLength": 2, "maxLength": 80},
            "bridge_cso_topic_id": {"type": "string", "format": "uuid"},
            "bridge_reasoning": {"type": "string", "minLength": 20, "maxLength": 300},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "recent_signals_summary",
            "persistent_tendencies_summary",
            "likely_dislikes_summary",
            "fusion_candidates",
            "deepening_seeds",
            "broadening_seeds",
        ],
        "properties": {
            "recent_signals_summary": {"type": "string", "maxLength": 400},
            "persistent_tendencies_summary": {"type": "string", "maxLength": 400},
            "likely_dislikes_summary": {"type": "string", "maxLength": 400},
            "fusion_candidates": {
                "type": "array",
                "maxItems": 3,
                "items": fusion_schema,
            },
            "deepening_seeds": {
                "type": "array",
                "maxItems": 3,
                "items": seed_schema,
            },
            "broadening_seeds": {
                "type": "array",
                "maxItems": 3,
                "items": seed_schema,
            },
        },
    }


USER_PROFILE_JSON_SCHEMA: dict[str, Any] = _build_user_profile_json_schema()


__all__ = [
    "USER_PROFILE_JSON_SCHEMA",
    "ActiveTraceSummary",
    "ArchivedTraceSummary",
    "CSOTopicCandidate",
    "CSOTopicCandidatePool",
    "FusionCandidate",
    "InterestStateSummary",
    "ProfileLLMInput",
    "TopicSeed",
    "UserProfilePayload",
]
