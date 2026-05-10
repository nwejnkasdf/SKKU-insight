"""check_error_codes.py — ErrorCode enum 값 ↔ docs/api/*.md 오류 표.

검증:
- ErrorCode 의 모든 값 (`auth.invalid_credentials` 등) 이 docs/api/*.md 중 어느 한 곳에라도
  코드 표 backtick 형식으로 등장.

(역방향 — docs 에만 있고 enum 에 없는 코드 — 는 별도로 보고하되 fail X.)
"""
from __future__ import annotations

import re
import sys

from scripts._common import repo_root, setup

setup()

from app.contracts import ErrorCode  # noqa: E402

DOCS_API_DIR = repo_root() / "docs" / "api"


def collect_docs_codes() -> set[str]:
    found: set[str] = set()
    pattern = re.compile(r"`([a-z]+\.[a-z_]+(?:\.[a-z_]+)?)`")
    for md in DOCS_API_DIR.glob("*.md"):
        for match in pattern.finditer(md.read_text(encoding="utf-8")):
            found.add(match.group(1))
    return found


def main() -> int:
    enum_values = {ec.value for ec in ErrorCode}
    docs_codes = collect_docs_codes()
    # 점 표기 코드 (`area.specific`)만 비교 — VALIDATION_ERROR 같은 단순 코드는 우회
    enum_dotted = {v for v in enum_values if "." in v}

    missing_in_docs = enum_dotted - docs_codes
    failed = bool(missing_in_docs)
    if missing_in_docs:
        print("[FAIL] ErrorCode 값이 docs/api/*.md 오류 표에 등장 안 함:")
        for v in sorted(missing_in_docs):
            print(f"  - {v}")
    # docs 에만 있는 코드는 정보 — 후속 에이전트가 enum 추가할 예정일 수 있음
    docs_dotted = {c for c in docs_codes if "." in c and "/" not in c}
    extra_in_docs = docs_dotted - enum_dotted
    # 노이즈 패턴 제거 (e.g. file 경로 sample 등)
    extra_in_docs = {c for c in extra_in_docs if len(c.split(".")) <= 3}
    if extra_in_docs:
        print("[INFO] docs/api/*.md 에는 있지만 ErrorCode enum 에 없는 코드 (검토 필요):")
        for v in sorted(extra_in_docs):
            print(f"  - {v}")

    print(f"\nenum 점 표기 {len(enum_dotted)} / docs dotted {len(docs_dotted)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
