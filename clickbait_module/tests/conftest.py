from __future__ import annotations

import pytest

from clickbait_module.app.settings import get_settings


@pytest.fixture(autouse=True)
def stub_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STUB_MODE", "true")
    monkeypatch.setenv("MERGED_MODEL_PATH", "/dummy/merged")
    monkeypatch.setenv("ADAPTER_PATH", "/dummy/adapter")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
