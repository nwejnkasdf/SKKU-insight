"""check_api_docs.py — OpenAPI export ↔ docs/api/*.md endpoint 표.

목적: backend FastAPI 의 path/method 합집합 ⊆ docs/api/*.md 에 표기된 path/method
합집합. 누락 시 exit 1.

(역방향 — docs 에만 있고 코드에 없는 경우 — 는 의도적으로 stub 단계에서 흔하므로 warning).
"""
from __future__ import annotations

import re
import sys

from scripts._common import repo_root, setup

setup()

from app.main import app  # noqa: E402

DOCS_API_DIR = repo_root() / "docs" / "api"

# `| METHOD | \`/path\` |` 형태 (api-conventions.md 표 표준)
ROW_PATTERN = re.compile(
    r"\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`(/[^`]+)`",
    re.IGNORECASE,
)

# FastAPI auto-generated / 인프라 endpoint — docs 검증 범위 외
CODE_PATH_WHITELIST = {
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/openapi.json",
    "/health",
}


def code_endpoints() -> set[tuple[str, str]]:
    """FastAPI app 의 (METHOD, path) 집합."""
    endpoints: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or not isinstance(methods, set):
            continue
        if path in CODE_PATH_WHITELIST:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            endpoints.add((method.upper(), path))
    return endpoints


def docs_endpoints() -> set[tuple[str, str]]:
    docs: set[tuple[str, str]] = set()
    for md_path in DOCS_API_DIR.glob("*.md"):
        text = md_path.read_text(encoding="utf-8")
        for match in ROW_PATTERN.finditer(text):
            method = match.group(1).upper()
            raw_path = match.group(2).strip()
            # query string / fragment 제거 — endpoint path 만 비교
            path = raw_path.split("?", 1)[0].split("#", 1)[0]
            docs.add((method, path))
    return docs


def normalize(path: str) -> str:
    """`{id}` ↔ `{user_id}` 같은 파라미터 명 차이를 흡수하지 않음 — 명시 일치 강제."""
    return path


def main() -> int:
    code_eps = code_endpoints()
    docs_eps = docs_endpoints()
    missing_in_docs = code_eps - docs_eps
    extra_in_docs = docs_eps - code_eps

    failed = False
    if missing_in_docs:
        print("[FAIL] 코드에는 있지만 docs/api/*.md 에 없는 endpoint:")
        for method, path in sorted(missing_in_docs):
            print(f"  - {method} {path}")
        failed = True
    if extra_in_docs:
        print(
            "[WARN] docs/api/*.md 에는 있지만 코드에 없음 (stub 단계 — 후속 에이전트가 채울 예정):"
        )
        for method, path in sorted(extra_in_docs):
            print(f"  - {method} {path}")
    print(f"\n코드 endpoint {len(code_eps)}건 / docs endpoint {len(docs_eps)}건")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
