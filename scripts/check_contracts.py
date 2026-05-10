"""check_contracts.py — contracts.py enum ↔ Alembic CHECK 제약 일치.

대상 enum 과 매핑된 alembic CHECK:
- AdminRole {super, operator, read_only} ↔ ck_admin_user_role
- TraversalStatus {active, stale, archived} ↔ ck_user_cso_traversal_status
- LeafTopicStatus {emerging, active, stale, merged, archived} ↔ (DynamicLeafTopic, A7)
- CollectionJobStatus {queued, running, succeeded, failed, skipped} ↔ (CollectionJob, A4)
- ContentType ↔ (Document, A4)

A2 1차 검증 대상 = AdminRole + TraversalStatus (A2 가 생성한 CHECK 만).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from scripts._common import repo_root, setup

setup()

from app.contracts import AdminRole, TraversalStatus  # noqa: E402

ALEMBIC_DIR = repo_root() / "backend" / "alembic" / "versions"

CHECKS = [
    {
        "name": "ck_admin_user_role",
        "enum_values": [r.value for r in AdminRole],
        "label": "AdminRole",
    },
    {
        "name": "ck_user_cso_traversal_status",
        "enum_values": [r.value for r in TraversalStatus],
        "label": "TraversalStatus",
    },
]


def main() -> int:
    failed = False
    for migration in sorted(ALEMBIC_DIR.glob("*.py")):
        text = migration.read_text(encoding="utf-8")
        for check in CHECKS:
            if check["name"] not in text:
                # 본 migration 이 해당 CHECK 를 정의하지 않으면 skip
                continue
            # CHECK 제약의 IN (...) 안 값들 추출
            pattern = re.compile(
                rf'name="{check["name"]}".*?"([^"]+\sIN\s*\([^)]+\))"', re.DOTALL
            )
            match = pattern.search(text)
            if not match:
                # 다른 패턴: CheckConstraint("role IN ('super',...)", name="ck_...")
                pattern2 = re.compile(
                    r'CheckConstraint\(\s*"([^"]+\sIN\s*\([^)]+\))"\s*,'
                    rf'\s*name="{check["name"]}"',
                    re.DOTALL,
                )
                match = pattern2.search(text)
            if not match:
                print(
                    f"[WARN] {migration.name} 에 CHECK 제약 {check['name']} "
                    "패턴 매칭 실패 — 정규식 점검 필요."
                )
                continue
            constraint_sql = match.group(1)
            for v in check["enum_values"]:
                if f"'{v}'" not in constraint_sql:
                    print(
                        f"[FAIL] {check['label']}.{v} 가 {migration.name} "
                        f"의 CHECK {check['name']} 에 없음."
                    )
                    failed = True
    print("\nCHECK 정합 검사 완료")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
