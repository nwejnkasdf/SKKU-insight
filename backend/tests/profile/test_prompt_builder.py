"""A8-v2 prompt_builder unit tests — system / user prompt + payload 직렬화 + token 추정.
"""
from __future__ import annotations

import json
from uuid import uuid4

from app.profile.prompt_builder import (
    build_system_prompt,
    build_user_prompt,
    estimate_prompt_tokens,
    to_input_payload,
)
from app.profile.schemas import (
    USER_PROFILE_JSON_SCHEMA,
    ActiveTraceSummary,
    ArchivedTraceSummary,
    CSOTopicCandidate,
    CSOTopicCandidatePool,
    ProfileLLMInput,
)


def _make_input() -> ProfileLLMInput:
    cso_a = uuid4()
    cso_b = uuid4()
    cso_c = uuid4()
    trace_id_active = uuid4()
    trace_id_archived = uuid4()
    return ProfileLLMInput(
        user_active_day_counter=47,
        active_traces=[
            ActiveTraceSummary(
                trace_id=trace_id_active,
                path_labels=["Systems", "OS", "Memory Mgmt"],
                path_cso_topic_ids=[cso_a, cso_b, cso_c],
                score_tail=0.72,
                last_activity_active_day=45,
            )
        ],
        archived_traces=[
            ArchivedTraceSummary(
                trace_id=trace_id_archived,
                path_labels=["Theory", "Algorithms", "Graph Algos"],
                path_cso_topic_ids=[uuid4(), uuid4(), uuid4()],
                score_tail_at_archive=0.83,
                last_activity_active_day=18,
                archived_at_active_day=25,
            )
        ],
        top_interest_states=[],
        recent_saved_topic_labels=["RLHF"],
        recent_hidden_topic_labels=[],
        not_interested_topic_labels=[],
        cso_candidate_pool=CSOTopicCandidatePool(
            fusion=[
                CSOTopicCandidate(cso_topic_id=cso_a, label="Systems"),
                CSOTopicCandidate(cso_topic_id=cso_b, label="OS"),
            ],
            deepening=[
                CSOTopicCandidate(cso_topic_id=cso_b, label="OS"),
                CSOTopicCandidate(cso_topic_id=cso_c, label="Memory Mgmt"),
            ],
            broadening=[
                CSOTopicCandidate(cso_topic_id=cso_a, label="Systems"),
            ],
        ),
    )


class TestSystemPrompt:
    def test_contains_nfr04_rejection(self) -> None:
        text = build_system_prompt("v1")
        # NFR-04 정합 — 점수/알고리즘 등 거부 키워드 명시.
        assert "점수" in text
        assert "알고리즘" in text
        assert "확률" in text

    def test_contains_cso_mapping_constraint(self) -> None:
        text = build_system_prompt("v1")
        # CSO 노드 ID 매핑 강제 instruction.
        assert "cso_candidate_pool" in text
        assert "bridge_cso_topic_id" in text

    def test_includes_generator_version(self) -> None:
        text = build_system_prompt("v42")
        assert "v42" in text


class TestUserPrompt:
    def test_user_prompt_contains_payload_json(self) -> None:
        llm_input = _make_input()
        payload = to_input_payload(llm_input)
        prompt = build_user_prompt(payload)
        # active_traces 가 prompt 안에 직렬화 포함.
        assert "Memory Mgmt" in prompt
        assert "Graph Algos" in prompt

    def test_user_prompt_echoes_output_schema(self) -> None:
        payload = to_input_payload(_make_input())
        prompt = build_user_prompt(payload)
        # output schema 의 핵심 필드 명이 prompt 끝에 echo.
        assert "fusion_candidates" in prompt
        assert "additionalProperties" in prompt


class TestPayloadSerialization:
    def test_to_input_payload_serializes_uuid_to_string(self) -> None:
        payload = to_input_payload(_make_input())
        # active_traces[0].trace_id 가 str (UUID 자동 직렬화).
        first = payload["active_traces"][0]
        assert isinstance(first["trace_id"], str)
        json.dumps(payload)  # roundtrip 가능 — 예외 시 fail.

    def test_to_input_payload_includes_cso_pool(self) -> None:
        payload = to_input_payload(_make_input())
        # 카테고리별 3 키 (C-44 P2-28).
        pool = payload["cso_candidate_pool"]
        assert isinstance(pool, dict)
        assert "fusion" in pool
        assert "deepening" in pool
        assert "broadening" in pool
        assert len(pool["fusion"]) == 2
        assert len(pool["deepening"]) == 2
        assert len(pool["broadening"]) == 1


class TestTokenBudget:
    def test_token_estimate_reasonable(self) -> None:
        payload = to_input_payload(_make_input())
        tokens = estimate_prompt_tokens(payload)
        assert 0 < tokens < 5000  # 단일 사용자 input — 5k token 미만.

    def test_schema_round_trip(self) -> None:
        """user prompt 안에 echo 된 schema 가 USER_PROFILE_JSON_SCHEMA 와 일치."""
        payload = to_input_payload(_make_input())
        prompt = build_user_prompt(payload)
        # JSON schema 가 prompt 안 직렬화 (markdown 외).
        schema_str = json.dumps(USER_PROFILE_JSON_SCHEMA, ensure_ascii=False)
        assert schema_str in prompt
