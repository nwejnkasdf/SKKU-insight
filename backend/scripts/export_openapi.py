"""FastAPI app 의 OpenAPI spec 을 stdout 에 JSON 으로 출력.

사용:
    cd backend
    python -m scripts.export_openapi > openapi.json

CI 의 codegen 파이프라인이 본 산출을 기반으로
client/src/generated/api.ts, admin-console/src/generated/api.ts 를 생성.
"""
from __future__ import annotations

import json
import sys

from app.main import app


def main() -> None:
    # Windows 기본 콘솔 인코딩(cp949) 회피 — 한국어 description/summary 안전 출력.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    spec = app.openapi()
    json.dump(spec, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
