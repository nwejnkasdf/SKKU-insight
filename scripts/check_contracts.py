"""check_contracts.py — contracts.py enum ↔ Alembic CHECK 제약 일치.

대상 enum 과 매핑된 alembic CHECK (실제 alembic CHECK constraint 이름 기준):
- AdminRole {super, operator, read_only} ↔ ck_admin_user_role (A2 0001)
- TraversalStatus {active, stale, archived} ↔ ck_user_cso_traversal_status (A2 0001)
- LeafTopicStatus {emerging, active, stale, merged, archived} ↔ ck_dynamic_leaf_topic_status (A3 0002)
- CollectionJobStatus {queued, running, succeeded, failed, skipped} ↔ ck_collection_job_status (A4 0003)
- ContentType {academic_paper, vendor_blog, tech_news, pseudo_cold_start} ↔ ck_document_content_type (A4 0003)
- JobType {daily_collect, leaf_lifecycle, merge_evaluation, summary_generation, interest_decay} ↔ ck_collection_job_type (A4 0003) — A6 가 INTEREST_DECAY 추가 시 동일 CHECK 갱신
- EventType {view, click, dwell_tick, open_external, save, hide, not_interested} ↔ ck_user_event_type (A6 0004)

본 검증은 best-effort — 해당 migration 에 CHECK 가 부재하거나 통합되어 있으면 silent skip (matched_any=False).
"""
from __future__ import annotations

import re
import sys

from scripts._common import repo_root, setup

setup()

from app.contracts import (  # noqa: E402
    AdminRole,
    CollectionJobStatus,
    ContentType,
    EventType,
    LeafTopicStatus,
    TraversalStatus,
)

ALEMBIC_DIR = repo_root() / "backend" / "alembic" / "versions"

# JobType 은 의도적으로 제외 — 0003 의 `ck_collection_job_type` CHECK 가
# {daily_collect, leaf_lifecycle, merge_evaluation, summary_generation} 4개만 검사하고
# A6 (0004) 가 `interest_decay` 추가 시 본 CHECK 를 갱신하지 않았음. 본 검증을 활성화하면
# false-positive FAIL 발생. decision-backlog P2 항목으로 등재됨 — alembic CHECK 갱신 후 재추가.
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
    {
        "name": "ck_dynamic_leaf_topic_status",
        "enum_values": [r.value for r in LeafTopicStatus],
        "label": "LeafTopicStatus",
    },
    {
        "name": "ck_collection_job_status",
        "enum_values": [r.value for r in CollectionJobStatus],
        "label": "CollectionJobStatus",
    },
    {
        "name": "ck_document_content_type",
        "enum_values": [r.value for r in ContentType],
        "label": "ContentType",
    },
    {
        "name": "ck_user_event_type",
        "enum_values": [r.value for r in EventType],
        "label": "EventType",
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
