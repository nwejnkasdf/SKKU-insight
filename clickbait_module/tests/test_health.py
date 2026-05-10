from __future__ import annotations

from fastapi.testclient import TestClient

from clickbait_module.app.main import app


def test_health_stub_mode() -> None:
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is False
    assert body["stub_mode"] is True
