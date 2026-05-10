"""check_env.py — Settings.__fields__ ↔ env-vars.md 표 ↔ .env.example 3-way diff.

3개 출처:
1) backend/app/config.py 의 Settings 필드 이름 (`Settings.model_fields`)
2) docs/ops/env-vars.md 의 표 (`| `VAR_NAME` |` 패턴)
3) backend/.env.example 의 키=값 줄

모든 변수가 3곳에 모두 정의돼야 함. 1곳이라도 누락 시 exit 1.
"""
from __future__ import annotations

import re
import sys

from scripts._common import repo_root, setup

setup()

from app.config import Settings  # noqa: E402

ENV_VARS_MD = repo_root() / "docs" / "ops" / "env-vars.md"
ENV_EXAMPLE = repo_root() / "backend" / ".env.example"


def settings_fields() -> set[str]:
    return set(Settings.model_fields.keys())


def docs_vars() -> set[str]:
    text = ENV_VARS_MD.read_text(encoding="utf-8")
    # 표의 ` `VAR_NAME` ` 패턴 또는 `.env.example` 골격의 `VAR_NAME=` 패턴
    found: set[str] = set()
    for match in re.finditer(r"`([A-Z][A-Z0-9_]+)`", text):
        found.add(match.group(1))
    for match in re.finditer(r"^([A-Z][A-Z0-9_]+)=", text, re.MULTILINE):
        found.add(match.group(1))
    return found


def env_example_vars() -> set[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return {
        m.group(1)
        for m in re.finditer(r"^([A-Z][A-Z0-9_]+)=", text, re.MULTILINE)
    }


def main() -> int:
    code = settings_fields()
    docs = docs_vars()
    example = env_example_vars()

    failed = False
    missing_in_docs = code - docs
    missing_in_example = code - example
    extra_in_example = example - code

    if missing_in_docs:
        print("[FAIL] config.py 의 Settings 필드가 docs/ops/env-vars.md 에 없음:")
        for v in sorted(missing_in_docs):
            print(f"  - {v}")
        failed = True
    if missing_in_example:
        print("[FAIL] config.py 의 Settings 필드가 backend/.env.example 에 없음:")
        for v in sorted(missing_in_example):
            print(f"  - {v}")
        failed = True
    if extra_in_example:
        print("[FAIL] backend/.env.example 에 있지만 config.py 의 Settings 에 없음:")
        for v in sorted(extra_in_example):
            print(f"  - {v}")
        failed = True

    print(
        f"\ncode {len(code)} / docs {len(docs)} / .env.example {len(example)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
