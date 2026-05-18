"""lifespan _validate_llm_provider 가드 — Codex round 2 S-08 + 2026-05-18 codex_oauth 본문.

A4 collection 은 mock + openai + codex_oauth 만 지원.
Anthropic/OpenRouter 는 여전히 boot 거부 (search_with_tools NotImplementedError).
codex_oauth 토글 시 추가로 `codex --version` binary 검증 (lifespan._validate_codex_cli).
"""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from app.contracts import LLMProviderType
from app.lifespan import _validate_llm_provider


class TestProviderGuard:
    def test_mock_passes(self) -> None:
        _validate_llm_provider(LLMProviderType.MOCK)  # no raise

    def test_openai_passes(self) -> None:
        _validate_llm_provider(LLMProviderType.OPENAI)  # no raise

    def test_anthropic_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="A4 collection 미지원"):
            _validate_llm_provider(LLMProviderType.ANTHROPIC)

    def test_openrouter_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="A4 collection 미지원"):
            _validate_llm_provider(LLMProviderType.OPENROUTER)

    def test_codex_oauth_allowed_with_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """codex_oauth + codex binary 존재 → boot 통과."""

        def _fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(
                args=["codex", "--version"],
                returncode=0,
                stdout=b"codex-cli 0.130.0\n",
                stderr=b"",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        _validate_llm_provider(LLMProviderType.CODEX_OAUTH)  # no raise

    def test_codex_oauth_blocked_when_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """codex_oauth + codex binary 없음 → RuntimeError (npm install 안내)."""

        def _fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[bytes]:
            raise FileNotFoundError("codex binary not in PATH")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        with pytest.raises(RuntimeError, match="codex CLI binary 없음"):
            _validate_llm_provider(LLMProviderType.CODEX_OAUTH)

    def test_codex_oauth_blocked_when_binary_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """codex_oauth + codex --version 이 exit code != 0 → RuntimeError."""

        def _fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(
                args=["codex", "--version"],
                returncode=2,
                stdout=b"",
                stderr=b"unknown error",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        with pytest.raises(RuntimeError, match=r"codex --version exit=2"):
            _validate_llm_provider(LLMProviderType.CODEX_OAUTH)
