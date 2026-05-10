from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from clickbait_module.app.main import app


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "document_id": str(uuid4()),
        "title": "충격! 놀라운 LLM 비밀",
        "body": "본문 내용",
        "source_name": "네이버뉴스",
        "source_type": "tech_news",
        "language": "ko",
        "meta": {},
    }
    base.update(overrides)
    return base


def test_classify_stub_response_shape() -> None:
    with TestClient(app) as client:
        r = client.post("/classify", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "clean"
    assert body["confidence"] == 0.5
    assert body["model_name"] == "ax-4.0-light-dora-clickbait-v1"
    assert body["adapter_type"] == "dora"
    assert "evaluated_at" in body


def test_classify_body_max_chars() -> None:
    with TestClient(app) as client:
        r = client.post("/classify", json=_payload(body="x" * 8001))
    assert r.status_code == 422
