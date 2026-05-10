"""check_schema.py — SQLAlchemy Base.metadata ↔ docs/data/schema.md 테이블명.

기본 수준 검증: 모든 모델의 __tablename__ 이 schema.md 에 등장하는지.
컬럼 단위 검증은 향후 강화 (autogenerate diff 와 중복).
"""
from __future__ import annotations

import sys

from scripts._common import repo_root, setup

setup()

from app.db import models  # noqa: E402, F401  Base.metadata 등록
from app.db.base import Base  # noqa: E402

SCHEMA_MD = repo_root() / "docs" / "data" / "schema.md"


def main() -> int:
    schema_text = SCHEMA_MD.read_text(encoding="utf-8")
    failed = False
    for table_name in Base.metadata.tables:
        token = f'__tablename__ = "{table_name}"'
        if token not in schema_text:
            print(f"[FAIL] table `{table_name}` 정의가 docs/data/schema.md 에 없음")
            failed = True
    print(f"\n검사한 테이블 {len(Base.metadata.tables)}건")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
