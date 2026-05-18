"""CodexOAuthProvider — `codex exec` subprocess wrap (2026-05-18 본문).

OpenAI Codex CLI (`@openai/codex`) 의 `codex exec --json` 비대화 모드를 wrap 해서
사용자 본인 ChatGPT Plus/Pro/Enterprise 구독 OAuth 로 LLM 호출. OpenAI 가 외부
도구에서 Codex OAuth 사용을 공식 허용 ([openclaw docs/concepts/oauth] +
[developers.openai.com/codex/cli/features] "Web search" 섹션).

방식 선택 근거 (토의 결정):
- 자체 PKCE OAuth 직접 구현 X — Codex CLI 가 이미 처리 (~/.codex/auth.json).
- subprocess wrap → endpoint 회전 시 codex CLI 업데이트로 자동 흡수.
- web_search 도 Codex 가 native Responses `web_search` tool 노출 — `--search` flag.
- structured JSON 응답 = `--output-schema` (우리 NFR-25/FR-44/FR-51 매핑).

호출 형식 (high slot, Chat Completions 동등):
    codex exec --json --ephemeral --skip-git-repo-check
        --ignore-user-config --ignore-rules    # backend prompt 가 SOR
        --sandbox read-only --cd <workdir>
        -c model=gpt-5.5
        -c model_reasoning_effort=high
        -c service_tier=fast                   # 시연 latency 최소화
        [--output-schema <file>]
        -                                       # stdin = prompt

search_with_tools (Responses + web_search 동등):
    codex [--search] exec --json --ephemeral --skip-git-repo-check
        --ignore-user-config --ignore-rules
        --sandbox read-only --cd <workdir>
        -c model=gpt-5.5
        -c model_reasoning_effort=high
        -c service_tier=fast
        --output-schema <SEARCH_SCHEMA>
        -

JSONL event stream:
    {"type":"thread.started","thread_id":"..."}
    {"type":"turn.started"}
    {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
    {"type":"item.completed","item":{"type":"web_search_call",...}}    # search 시
    {"type":"turn.completed","usage":{"input_tokens":N, "output_tokens":M,
                                     "cached_input_tokens":K,
                                     "reasoning_output_tokens":R}}

usage 매핑 (LLMResponse):
- prompt_tokens = input_tokens
- completion_tokens = output_tokens + reasoning_output_tokens (reasoning 도 사용량 차감)

위험·운영 고려:
- ~/.codex/auth.json refresh token 만료 시 codex 가 401. lifespan health check 가
  `codex login status` 로 사전 검증.
- sandbox=read-only + --ephemeral + --cd 격리 → codex 가 backend 컨테이너 파일
  의도치 않게 읽거나 쓰는 위험 차단.
- web_search 는 default cached (`CODEX_WEB_SEARCH_MODE=cached`). 시연에서
  최신성 요구 시 `live` 로 토글.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from app.config import get_settings
from app.llm_provider._concurrency import (
    acquire_slot,
    check_token_budget,
    record_token_usage,
)
from app.llm_provider.protocol import (
    ChatMessage,
    LLMBudgetExceeded,
    LLMResponse,
    ModelSlot,
    ProviderError,
    ResponseFormat,
    SearchResult,
)
from app.redis import get_redis

logger = logging.getLogger(__name__)

# search_with_tools 의 SearchResult list JSON Schema — Codex 가 final response 를
# 본 schema 로 강제. (NFR-25 정합 — `abstract_summary` 가 LLM self-summary.)
_SEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "url", "abstract_summary"],
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "abstract_summary": {"type": "string"},
                    "publisher_domain": {"type": ["string", "null"]},
                    "publisher_label": {"type": ["string", "null"]},
                    "published_at": {"type": ["string", "null"]},
                    "doi": {"type": ["string", "null"]},
                    "canonical_url": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "raw": {"type": "object"},
                },
            },
        }
    },
}


def _format_messages_as_prompt(messages: list[ChatMessage]) -> str:
    """ChatMessage list → 단일 prompt 문자열 (codex exec stdin).

    codex 는 prompt 를 plain text 로 받아 user turn 으로 처리. system message 는
    명시 marker (`[system]`) + 본문 형태로 prepend. role 별 marker 가 codex 내부
    파싱 의도가 아니라 단순 가독성 — codex 가 final response 만 생성하면 충분.
    """
    parts: list[str] = []
    for m in messages:
        if m.role == "system":
            parts.append(f"[system]\n{m.content}")
        elif m.role == "user":
            parts.append(f"[user]\n{m.content}")
        elif m.role == "assistant":
            parts.append(f"[assistant]\n{m.content}")
    return "\n\n".join(parts)


def _resolve_reasoning_effort(model_slot: ModelSlot) -> str:
    settings = get_settings()
    if model_slot == "high":
        return settings.LLM_REASONING_EFFORT_HIGH
    return settings.LLM_REASONING_EFFORT_MEDIUM


def _resolve_model_name(model_slot: ModelSlot) -> str:
    settings = get_settings()
    if model_slot == "high":
        return settings.LLM_MODEL_HIGH
    return settings.LLM_MODEL_MEDIUM


def _ensure_workdir() -> Path:
    """codex 가 사용할 격리된 작업 디렉토리 (--cd). exists() 보장."""
    settings = get_settings()
    workdir = Path(settings.CODEX_WORKDIR)
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _build_base_argv(
    *,
    model_name: str,
    reasoning_effort: str,
    output_schema_path: Path | None,
    enable_search: bool,
) -> list[str]:
    """codex exec 명령 argv 조립.

    `--search` 는 global flag (codex --search exec ...). 다른 옵션은 exec subcommand.
    """
    settings = get_settings()
    argv: list[str] = [settings.CODEX_CLI_PATH]
    if enable_search:
        argv.append("--search")
    argv += [
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        # 2026-05-18 사용자 결정 — codex 의 사용자별 personality / `.rules` 무시.
        # 우리 backend prompt 가 SOR (NFR-04 마스킹 / 한국어 응답 / FR-44 reason
        # 형식 등 자체 제어). codex `~/.codex/config.toml` 의 personality·prompt
        # 가 응답 스타일에 잡스러운 영향 주는 것 차단. `-c key=value` override 는
        # 우선순위 높아서 그대로 적용됨 (호스트 실측 검증).
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        settings.CODEX_SANDBOX_MODE,
        "--cd",
        str(_ensure_workdir()),
        "-c",
        f"model={model_name}",
        "-c",
        f"model_reasoning_effort={reasoning_effort}",
        "-c",
        f"service_tier={settings.CODEX_SERVICE_TIER}",
    ]
    if output_schema_path is not None:
        argv += ["--output-schema", str(output_schema_path)]
    # stdin 으로 prompt — `-` sentinel.
    argv.append("-")
    return argv


async def _run_codex_subprocess(
    argv: list[str],
    *,
    stdin_bytes: bytes,
    timeout_seconds: float,
) -> tuple[bytes, bytes]:
    """codex exec subprocess 실행. timeout 초과 시 kill + ProviderError.

    return: (stdout, stderr) bytes.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
    except FileNotFoundError as exc:
        raise ProviderError(
            f"codex_cli_not_found: {argv[0]!r} — `npm i -g @openai/codex` 후 재시도"
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin_bytes), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        raise ProviderError(
            f"codex_exec_timeout: {timeout_seconds}s exceeded"
        ) from exc

    if proc.returncode != 0:
        snippet = stderr[:500].decode("utf-8", errors="replace").strip()
        raise ProviderError(
            f"codex_exec_failed: exit={proc.returncode} stderr={snippet!r}"
        )
    return stdout, stderr


def _parse_jsonl_events(stdout_bytes: bytes) -> tuple[str, dict[str, int]]:
    """codex JSONL event stream → (final_text, usage_dict).

    - agent_message item 의 text 를 순서대로 합쳐 final_text 구성.
    - turn.completed event 의 usage 객체 그대로 반환.
    - JSON parse 실패 line 은 skip + warn (codex 가 정상 라인에 섞어 stderr 로
      progress 출력하는 케이스 방어).

    raise ProviderError — agent_message 0건 (codex 가 응답 못 만든 경우).
    """
    text_chunks: list[str] = []
    usage: dict[str, int] = {}
    for raw_line in stdout_bytes.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(
                "codex_oauth: non-JSON line skipped: %r",
                line[:200],
            )
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text", "")
                if isinstance(text, str) and text:
                    text_chunks.append(text)
        elif event_type == "turn.completed":
            raw_usage = event.get("usage", {})
            if isinstance(raw_usage, dict):
                for k, v in raw_usage.items():
                    if isinstance(v, int):
                        usage[k] = v
    if not text_chunks:
        raise ProviderError(
            "codex_exec_no_agent_message: stdout 에 agent_message item 없음"
        )
    return "".join(text_chunks), usage


def _map_usage_to_tokens(usage: dict[str, int]) -> tuple[int, int]:
    """codex usage → (prompt_tokens, completion_tokens).

    reasoning_output_tokens 도 사용량으로 차감 (budget guard 정합).
    """
    prompt = int(usage.get("input_tokens", 0))
    completion = int(usage.get("output_tokens", 0)) + int(
        usage.get("reasoning_output_tokens", 0)
    )
    return prompt, completion


class CodexOAuthProvider:
    """`codex exec` subprocess wrap. OpenAI 공식 허용 path (subscription OAuth).

    `LLM_PROVIDER=codex_oauth` 토글 시 사용. ~/.codex/auth.json 이 호스트 (docker
    volume mount) 에 존재 + `codex login status` OK 가 boot 전제.
    """

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model_slot: ModelSlot,
        response_format: ResponseFormat = "text",
        max_tokens: int | None = None,
        temperature: float = 0.2,
        user_id: str | None = None,
    ) -> LLMResponse:
        settings = get_settings()
        redis = get_redis("default")
        if not await check_token_budget(redis):
            raise LLMBudgetExceeded()

        model_name = _resolve_model_name(model_slot)
        reasoning_effort = _resolve_reasoning_effort(model_slot)
        prompt = _format_messages_as_prompt(messages)
        # `_` placeholder for unused (signature 호환).
        _ = max_tokens, temperature

        with _maybe_output_schema(response_format) as schema_path:
            argv = _build_base_argv(
                model_name=model_name,
                reasoning_effort=reasoning_effort,
                output_schema_path=schema_path,
                enable_search=False,
            )
            async with acquire_slot(user_id):
                stdout, _stderr = await _run_codex_subprocess(
                    argv,
                    stdin_bytes=prompt.encode("utf-8"),
                    timeout_seconds=settings.LLM_REQUEST_TIMEOUT_SECONDS,
                )

        text, usage = _parse_jsonl_events(stdout)
        prompt_tokens, completion_tokens = _map_usage_to_tokens(usage)
        await record_token_usage(prompt_tokens + completion_tokens, redis)

        parsed_json: Any | None = None
        if response_format == "json":
            try:
                parsed_json = json.loads(text)
            except json.JSONDecodeError:
                parsed_json = None
        return LLMResponse(
            text=text,
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason="stop",
            parsed_json=parsed_json,
            meta={
                "provider": "codex_oauth",
                "reasoning_effort": reasoning_effort,
                "cached_input_tokens": usage.get("cached_input_tokens", 0),
                "reasoning_output_tokens": usage.get(
                    "reasoning_output_tokens", 0
                ),
            },
        )

    async def search_with_tools(
        self,
        trace_json: dict[str, Any],
        leaf_label: str,
        *,
        top_n: int = 10,
        user_id: str | None = None,
    ) -> list[SearchResult]:
        """codex exec --search + --output-schema 로 web_search → SearchResult list.

        prompt 형식은 openai.py:search_with_tools 와 동일 (SYSTEM_PROMPT_TEMPLATE
        재사용) — fixture 호환 + NFR-25 정합 유지.
        """
        from app.collection.llm_search import SYSTEM_PROMPT_TEMPLATE

        settings = get_settings()
        redis = get_redis("default")
        if not await check_token_budget(redis):
            raise LLMBudgetExceeded()

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(top_n=top_n)
        user_prompt = json.dumps(
            {"trace": trace_json, "leaf_label": leaf_label, "top_n": top_n},
            ensure_ascii=False,
        )
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_prompt),
        ]
        prompt = _format_messages_as_prompt(messages)

        # search_with_tools 는 항상 high slot.
        model_name = settings.LLM_MODEL_HIGH
        reasoning_effort = settings.LLM_REASONING_EFFORT_HIGH
        enable_search = settings.CODEX_WEB_SEARCH_MODE.lower() == "live"

        with _write_temp_schema(_SEARCH_OUTPUT_SCHEMA) as schema_path:
            argv = _build_base_argv(
                model_name=model_name,
                reasoning_effort=reasoning_effort,
                output_schema_path=schema_path,
                enable_search=enable_search,
            )
            async with acquire_slot(user_id):
                stdout, _stderr = await _run_codex_subprocess(
                    argv,
                    stdin_bytes=prompt.encode("utf-8"),
                    timeout_seconds=settings.LLM_REQUEST_TIMEOUT_SECONDS,
                )

        text, usage = _parse_jsonl_events(stdout)
        prompt_tokens, completion_tokens = _map_usage_to_tokens(usage)
        await record_token_usage(prompt_tokens + completion_tokens, redis)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"codex_search_json_parse_error: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderError(
                f"codex_search_response_not_object: {type(parsed).__name__}"
            )
        results_raw = parsed.get("results", [])
        if not isinstance(results_raw, list):
            raise ProviderError("codex_search_response_missing_results_list")
        results: list[SearchResult] = []
        for item in results_raw[:top_n]:
            if not isinstance(item, dict):
                continue
            results.append(_parse_search_item(item))
        return results


def _parse_search_item(item: dict[str, Any]) -> SearchResult:
    """openai.py:_parse_search_item 의 logic 동등 — published_at 변환 포함."""
    published_at_raw = item.get("published_at")
    published_at: datetime | None = None
    if isinstance(published_at_raw, str) and published_at_raw:
        normalized = published_at_raw.rstrip("Z")
        if published_at_raw.endswith("Z"):
            normalized = normalized + "+00:00"
        try:
            published_at = datetime.fromisoformat(normalized)
        except ValueError:
            published_at = None
    raw_field = item.get("raw", {})
    raw_dict: dict[str, Any] = dict(raw_field) if isinstance(raw_field, dict) else {}
    return SearchResult(
        title=str(item.get("title", "")),
        url=str(item.get("url", "")),
        abstract_summary=str(item.get("abstract_summary", "")),
        publisher_domain=item.get("publisher_domain"),
        publisher_label=item.get("publisher_label"),
        published_at=published_at,
        doi=item.get("doi"),
        canonical_url=item.get("canonical_url"),
        confidence=float(item.get("confidence", 0.8)),
        raw=raw_dict,
    )


class _maybe_output_schema:
    """response_format='json' 일 때만 임시 schema 파일 생성 + cleanup.

    text 모드는 schema 없이 자유 응답. JSON 모드는 generic object schema 강제
    (caller 가 더 엄격한 schema 원하면 future enhancement — Settings 에 schema
    경로 직접 지정 가능하게).
    """

    _GENERIC_JSON_SCHEMA: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": True,
    }

    def __init__(self, response_format: ResponseFormat) -> None:
        self._response_format = response_format
        self._path: Path | None = None
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path | None:
        if self._response_format != "json":
            return None
        self._tmpdir = tempfile.TemporaryDirectory(prefix="codex-schema-")
        self._path = Path(self._tmpdir.name) / "schema.json"
        self._path.write_text(
            json.dumps(self._GENERIC_JSON_SCHEMA), encoding="utf-8"
        )
        return self._path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
        self._path = None
        self._tmpdir = None


class _write_temp_schema:
    """search_with_tools 의 SEARCH_OUTPUT_SCHEMA 임시 파일."""

    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._path: Path | None = None

    def __enter__(self) -> Path:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="codex-schema-")
        self._path = Path(self._tmpdir.name) / "schema.json"
        self._path.write_text(json.dumps(self._schema), encoding="utf-8")
        return self._path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
        self._path = None
        self._tmpdir = None


__all__ = ["CodexOAuthProvider"]
