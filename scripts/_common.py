"""check_*.py 공통 유틸 — repo root 자동 검출 + backend sys.path 셋팅.

각 check 스크립트가 동일 패턴으로 부트:
```python
from scripts._common import setup
setup()
```
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root() -> Path:
    """이 파일은 <repo>/scripts/_common.py 이므로 부모가 repo root."""
    return Path(__file__).resolve().parent.parent


def setup() -> None:
    """backend/ 를 sys.path 에 추가해 `import app.*` 가능하게 함.

    또한 TESTING=1 환경에서 BaseSettings 가 .env 없이도 default 값으로 로드되도록
    여러 필수 secret 의 default 를 주입 (CI 환경).
    """
    backend = repo_root() / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    # CI 환경에서 .env 없이도 검증 가능하도록 dummy 시크릿 주입
    os.environ.setdefault("JWT_SECRET", "x" * 64)
    os.environ.setdefault("POSTGRES_PASSWORD", "dummy-check-password")
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+asyncpg://insight:dummy@postgres:5432/insight"
    )


__all__ = ["repo_root", "setup"]
