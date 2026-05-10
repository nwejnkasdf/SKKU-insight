from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from clickbait_module.app.schemas import ClassifyMeta, ClassifyRequest
from clickbait_module.app.settings import Settings
from clickbait_module.app.shim import build_prompt, derive_category, to_classify_response


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        MERGED_MODEL_PATH="/dummy/merged",
        ADAPTER_PATH=None,
        STUB_MODE=True,
    )
    base.update(overrides)
    return Settings(**base)


def _request(**overrides: Any) -> ClassifyRequest:
    base: dict[str, Any] = dict(
        document_id=uuid4(),
        title="제목",
        body="본문",
        source_name="네이버뉴스",
        source_type="tech_news",
        language="ko",
        meta=ClassifyMeta(),
    )
    base.update(overrides)
    return ClassifyRequest(**base)


class _MockTokenizer:
    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        tokenize: bool = False,
        add_generation_prompt: bool = True,
    ) -> str:
        return "\n---\n".join(f"[{m['role']}]\n{m['content']}" for m in messages) + "\n[assistant]\n"


def test_derive_category_naver_falls_back() -> None:
    assert derive_category("네이버뉴스", "ETC") == "ETC"


def test_derive_category_empty_falls_back() -> None:
    assert derive_category("", "ETC") == "ETC"


def test_derive_category_arbitrary_falls_back() -> None:
    assert derive_category("TechCrunch", "ETC") == "ETC"


def test_to_classify_response_clickbait() -> None:
    settings = _settings(CLICKBAIT_THRESHOLD=0.5)
    resp = to_classify_response(0.8, settings)
    assert resp.decision == "clickbait"
    assert resp.confidence == pytest.approx(0.8)
    assert resp.model_name == "ax-4.0-light-dora-clickbait-v1"
    assert resp.adapter_type == "dora"


def test_to_classify_response_clean() -> None:
    settings = _settings(CLICKBAIT_THRESHOLD=0.5)
    resp = to_classify_response(0.3, settings)
    assert resp.decision == "clean"
    assert resp.confidence == pytest.approx(0.7)


def test_to_classify_response_threshold_override() -> None:
    settings = _settings(CLICKBAIT_THRESHOLD=0.4)
    resp = to_classify_response(0.45, settings)
    assert resp.decision == "clickbait"
    assert resp.confidence == pytest.approx(0.55)


def test_build_prompt_includes_system_and_article_blocks() -> None:
    settings = _settings(CATEGORY_FALLBACK="ETC")
    req = _request(title="충격! 비밀", body="본문 내용")
    prompt = build_prompt(req, _MockTokenizer(), settings)
    assert "낚시성(clickbait) 여부" in prompt
    assert "[카테고리]\nETC" in prompt
    assert "[제목]\n충격! 비밀" in prompt
    assert "[본문]\n본문 내용" in prompt
    assert "이 기사가 낚시성이 강하면 1, 그렇지 않으면 0만 출력하라" in prompt
