"""check_redis_keys.py — RedisKey SOR 외부 raw f-string 사용 검출.

contracts.py 의 RedisKey static 메서드를 거치지 않고 직접 `f"refresh:..."` 같이
하드코딩된 Redis 키 패턴을 사용하는 위치를 검출. CI 에서 강제.

화이트리스트:
- backend/app/contracts.py — RedisKey 정의 자체
- 본 스크립트 자체 + tests/fixtures
- 명시 화이트리스트된 keys (예: account_deletion:{user_id} — Plan §account-deletion lock)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from scripts._common import repo_root, setup

setup()

REDIS_KEY_PREFIXES = [
    "refresh:",
    "refresh_index:",
    "jwt_denylist:",
    "recommendation:",
    "lock:recommendation_build:",
    "lock:traversal:",
    "lock:collection:",
    "lock:onboarding:",
    "consent:active:",
    "cold_start:status:",
    "rl:",
    "llm:tokens:",
    "llm:active:",              # C-19 LLM 분산 semaphore (global + per-user)
    "dwell:",
    "events:buffer:",
    "system_config:",           # A6 SystemConfig 캐시 (lifespan 1회 로드)
    "lock:interest_decay:",     # A6 daily cron lock (18 UTC = 03 KST)
    "event:dup:",               # A6 payload-hash idempotency (200 match / 409 mismatch)
    "cso:clusters:",            # A3 12 cluster cache (cso_topic 임포트 후 SETEX)
]

# A2 가 의도적으로 contracts.py 외에서 사용하는 키 (Plan §4 account-deletion lock,
# §5 onboarding lock companion key, §security idempotency key)
EXPLICIT_ALLOWED_KEYS = [
    "account_deletion:",       # consent.service Redis lock — Plan §4
    "idemp:",                  # security.idempotency 캐시 prefix
]

BACKEND_DIR = repo_root() / "backend"
SCAN_DIR = BACKEND_DIR / "app"


def find_violations() -> list[tuple[Path, int, str]]:
    """raw f-string Redis 키 사용 위치 검출.

    무시 대상:
    - wildcard `*` 포함 SCAN 패턴 (RedisKey 의 정확 키 생성 범위 외)
    - EXPLICIT_ALLOWED_KEYS prefix (`account_deletion:`, `idemp:`)
    """
    violations: list[tuple[Path, int, str]] = []
    pattern_prefixes = REDIS_KEY_PREFIXES
    pattern_alt = "|".join(re.escape(p) for p in pattern_prefixes)
    fstring_regex = re.compile(
        r'f["\\\']((?:' + pattern_alt + r')[^"\\\']*)["\\\']'
    )
    for py_path in SCAN_DIR.rglob("*.py"):
        if py_path.name == "contracts.py":
            continue
        text = py_path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            m = fstring_regex.search(line)
            if not m:
                continue
            matched_key = m.group(1)
            # SCAN 와일드카드 패턴 — RedisKey SOR 범위 외 (prefix 매칭, 정확 키 X)
            if "*" in matched_key:
                continue
            # 명시 허용 키 (account_deletion:, idemp:) — A2 결정으로 RedisKey 외 사용 허가
            if any(allowed in matched_key for allowed in EXPLICIT_ALLOWED_KEYS):
                continue
            violations.append((py_path, line_no, line.strip()))
    return violations


def main() -> int:
    violations = find_violations()
    failed = bool(violations)
    if violations:
        print("[FAIL] contracts.py RedisKey 외부 raw f-string Redis 키 사용:")
        for path, line, snippet in violations:
            print(f"  - {path.relative_to(BACKEND_DIR)}:{line}  {snippet}")
        print(
            "\n위 위치는 RedisKey static method 를 사용해야 합니다 "
            "(headers/contracts.py)."
        )
    else:
        print("[OK] 모든 Redis 키 RedisKey SOR 경유.")

    print(
        f"\nallowed extra (not enforced): {', '.join(EXPLICIT_ALLOWED_KEYS)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
