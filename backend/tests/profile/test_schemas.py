"""A8-v2 (formerly A9) UserProfile schema 검증 — Pydantic strict + JSONSchema spec.

USER_PROFILE_JSON_SCHEMA 가 codex `--output-schema` / openai `response_format=json_schema`
양쪽 strict 요건을 충족하는지 검증 (additionalProperties=False, required, length 제한).
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import jsonschema  # type: ignore[import-untyped]
import pytest
from pydantic import ValidationError

from app.profile.schemas import (
    USER_PROFILE_JSON_SCHEMA,
    FusionCandidate,
    TopicSeed,
    UserProfilePayload,
)


def _make_valid_fusion(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "from_archived": ["Graph Algorithms"],
        "from_active": ["Memory Management"],
        "bridge_label": "Memory-bounded Algorithms",
        "bridge_cso_topic_id": str(uuid4()),
        "bridge_reasoning": (
            "두 영역이 만나는 학습 path — 메모리 제약 하의 그래프 알고리즘."
        ),
    }
    base.update(overrides)
    return base


def _make_valid_seed(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "cso_topic_id": str(uuid4()),
        "label": "Distributed Systems",
    }
    base.update(overrides)
    return base


def _make_valid_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "recent_signals_summary": "최근 2주 시스템·메모리 관리에 집중.",
        "persistent_tendencies_summary": "Systems 와 Theory 두 cluster 를 깊이 탐색.",
        "likely_dislikes_summary": "vendor blog marketing 톤에 약함.",
        "fusion_candidates": [_make_valid_fusion()],
        "deepening_seeds": [_make_valid_seed()],
        "broadening_seeds": [_make_valid_seed(label="Formal Verification")],
    }
    base.update(overrides)
    return base


class TestPydanticValidation:
    def test_valid_payload_passes(self) -> None:
        payload = _make_valid_payload()
        result = UserProfilePayload.model_validate(payload)
        assert len(result.fusion_candidates) == 1
        assert result.fusion_candidates[0].bridge_label == "Memory-bounded Algorithms"

    def test_empty_arrays_allowed(self) -> None:
        payload = _make_valid_payload(
            fusion_candidates=[],
            deepening_seeds=[],
            broadening_seeds=[],
        )
        result = UserProfilePayload.model_validate(payload)
        assert result.fusion_candidates == []
        assert result.deepening_seeds == []
        assert result.broadening_seeds == []

    def test_fusion_max_3_enforced(self) -> None:
        payload = _make_valid_payload(
            fusion_candidates=[_make_valid_fusion() for _ in range(4)]
        )
        with pytest.raises(ValidationError):
            UserProfilePayload.model_validate(payload)

    def test_fusion_bridge_reasoning_min_length_20(self) -> None:
        payload = _make_valid_payload(
            fusion_candidates=[
                _make_valid_fusion(bridge_reasoning="너무 짧음")
            ]
        )
        with pytest.raises(ValidationError):
            UserProfilePayload.model_validate(payload)

    def test_fusion_invalid_uuid_rejected(self) -> None:
        payload = _make_valid_payload(
            fusion_candidates=[
                _make_valid_fusion(bridge_cso_topic_id="not-a-uuid")
            ]
        )
        with pytest.raises(ValidationError):
            UserProfilePayload.model_validate(payload)

    def test_extra_fields_forbidden(self) -> None:
        payload = _make_valid_payload()
        payload["unexpected_field"] = "should be rejected"
        with pytest.raises(ValidationError):
            UserProfilePayload.model_validate(payload)

    def test_recent_signals_summary_max_400(self) -> None:
        payload = _make_valid_payload(recent_signals_summary="가" * 401)
        with pytest.raises(ValidationError):
            UserProfilePayload.model_validate(payload)

    def test_fusion_from_archived_min_1(self) -> None:
        payload = _make_valid_payload(
            fusion_candidates=[_make_valid_fusion(from_archived=[])]
        )
        with pytest.raises(ValidationError):
            UserProfilePayload.model_validate(payload)

    def test_fusion_candidate_class_strict(self) -> None:
        # Pydantic 직접 검증 (UserProfilePayload 통하지 않고).
        bad = _make_valid_fusion()
        bad["extra"] = "x"
        with pytest.raises(ValidationError):
            FusionCandidate.model_validate(bad)

    def test_topic_seed_class_strict(self) -> None:
        bad = _make_valid_seed()
        bad["extra"] = "x"
        with pytest.raises(ValidationError):
            TopicSeed.model_validate(bad)


class TestJSONSchema:
    def test_schema_is_valid_jsonschema_draft(self) -> None:
        """USER_PROFILE_JSON_SCHEMA 가 정상 JSONSchema 객체."""
        jsonschema.Draft202012Validator.check_schema(USER_PROFILE_JSON_SCHEMA)

    def test_schema_additional_properties_false(self) -> None:
        assert USER_PROFILE_JSON_SCHEMA["additionalProperties"] is False
        fusion_items = USER_PROFILE_JSON_SCHEMA["properties"][
            "fusion_candidates"
        ]["items"]
        assert fusion_items["additionalProperties"] is False
        seed_items = USER_PROFILE_JSON_SCHEMA["properties"]["deepening_seeds"][
            "items"
        ]
        assert seed_items["additionalProperties"] is False

    def test_schema_all_fields_required(self) -> None:
        """codex --output-schema 호환 — 모든 properties 가 required list 에."""
        required = USER_PROFILE_JSON_SCHEMA["required"]
        expected = {
            "recent_signals_summary",
            "persistent_tendencies_summary",
            "likely_dislikes_summary",
            "fusion_candidates",
            "deepening_seeds",
            "broadening_seeds",
        }
        assert set(required) == expected

    def test_schema_validates_valid_payload(self) -> None:
        payload = _make_valid_payload()
        jsonschema.validate(payload, USER_PROFILE_JSON_SCHEMA)

    def test_schema_rejects_extra_field(self) -> None:
        payload = _make_valid_payload()
        payload["extra"] = "x"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, USER_PROFILE_JSON_SCHEMA)
