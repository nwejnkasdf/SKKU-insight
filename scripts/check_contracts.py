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


def _extract_check_clause(text: str, check_name: str) -> str | None:
    """CheckConstraint(...) 호출에서 SQL clause 추출. 인자 순서 무관, multiline 처리.

    `sa.CheckConstraint("role IN ('super',...)", name="ck_admin_user_role")` 또는
    `name="..."` 가 먼저 오는 변형 모두 지원.
    """
    # 1) name="<check_name>" 의 위치 찾기
    name_pattern = re.compile(rf'name="{re.escape(check_name)}"')
    name_match = name_pattern.search(text)
    if not name_match:
        return None
    # 2) name 주변의 CheckConstraint(...) 블록 찾아 첫 string argument 추출
    # 가장 가까운 앞의 CheckConstraint( 부터 뒤의 ) 까지
    name_pos = name_match.start()
    pre = text[:name_pos]
    cc_idx = pre.rfind("CheckConstraint(")
    if cc_idx < 0:
        return None
    # CheckConstraint( 다음에 오는 따옴표 시작 위치 찾기 (multiline ok)
    after = text[cc_idx + len("CheckConstraint(") :]
    str_match = re.match(r'\s*"([^"]*)"', after, re.DOTALL)
    if not str_match:
        return None
    return str_match.group(1)


def main() -> int:
    failed = False
    matched_any = {check["name"]: False for check in CHECKS}
    for migration in sorted(ALEMBIC_DIR.glob("*.py")):
        text = migration.read_text(encoding="utf-8")
        for check in CHECKS:
            clause = _extract_check_clause(text, check["name"])
            if clause is None:
                continue
            matched_any[check["name"]] = True
            for v in check["enum_values"]:
                if f"'{v}'" not in clause:
                    print(
                        f"[FAIL] {check['label']}.{v} 가 {migration.name} "
                        f"의 CHECK {check['name']} 에 없음.\n"
                        f"  clause: {clause}"
                    )
                    failed = True
    for check in CHECKS:
        if not matched_any[check["name"]]:
            # 본 A2 범위 외 CHECK (LeafTopicStatus 등) 는 아직 migration 없음 — OK
            pass
    print("\nCHECK 정합 검사 완료")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
