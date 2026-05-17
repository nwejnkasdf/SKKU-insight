"""check_contracts.py — contracts.py enum ↔ Alembic CHECK 제약 일치.

대상 enum 과 매핑된 alembic CHECK (실제 alembic CHECK constraint 이름 기준):
- AdminRole {super, operator, read_only} ↔ ck_admin_user_role (A2 0001)
- TraversalStatus {active, stale, archived} ↔ ck_user_cso_traversal_status (A2 0001)
- LeafTopicStatus {emerging, active, stale, merged, archived} ↔ ck_dynamic_leaf_topic_status (A3 0002)
- CollectionJobStatus {queued, running, succeeded, failed, skipped} ↔ ck_collection_job_status (A4 0003)
- ContentType {academic_paper, vendor_blog, tech_news, pseudo_cold_start} ↔ ck_document_content_type (A4 0003)
- JobType {daily_collect, leaf_lifecycle, merge_evaluation, summary_generation, interest_decay, trace_merge} ↔ ck_collection_job_type (A7 0005 갱신 — A6 P2-21 해소)
- EventType {view, click, dwell_tick, open_external, save, hide, not_interested} ↔ ck_user_event_type (A6 0004)

본 검증은 best-effort — 해당 migration 에 CHECK 가 부재하거나 통합되어 있으면 silent skip (matched_any=False).
스크립트는 모든 migration 파일을 순회하면서 **마지막** CheckConstraint 매칭값을 사용 (drop+recreate 패턴 안전).
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
    JobType,
    LeafTopicStatus,
    TraversalStatus,
)

ALEMBIC_DIR = repo_root() / "backend" / "alembic" / "versions"

# JobType 검증 활성화 (A7 0005 가 ck_collection_job_type 을 6-value 로 갱신, P2-21 해소).
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
    {
        "name": "ck_collection_job_type",
        "enum_values": [r.value for r in JobType],
        "label": "JobType",
    },
]


def _extract_check_clause(text: str, check_name: str) -> str | None:
    """CheckConstraint(...) 또는 op.create_check_constraint(...) 에서 SQL clause 추출.

    두 패턴 지원:
    1. `sa.CheckConstraint("role IN ('super',...)", name="ck_admin_user_role")`
       (또는 `name=...` 가 먼저 오는 변형) — ORM/initial migration 패턴.
    2. `op.create_check_constraint("ck_collection_job_type", "table", "job_type IN (...)")`
       — drop+recreate 갱신 패턴 (A7 0005).

    각 패턴별로 마지막 매칭만 반환 (한 migration 안 같은 이름 중복은 통상 없음).
    """
    # 패턴 1: CheckConstraint("clause", name="<check_name>") 또는 name first.
    name_pattern = re.compile(rf'name="{re.escape(check_name)}"')
    name_match = name_pattern.search(text)
    if name_match:
        name_pos = name_match.start()
        pre = text[:name_pos]
        cc_idx = pre.rfind("CheckConstraint(")
        if cc_idx >= 0:
            after = text[cc_idx + len("CheckConstraint(") :]
            str_match = re.match(r'\s*"([^"]*)"', after, re.DOTALL)
            if str_match:
                return str_match.group(1)

    # 패턴 2: op.create_check_constraint("<check_name>", "<table>", "<clause>", ...)
    # 첫 번째 string argument 가 constraint name, 세 번째 가 clause (multiline ok).
    create_pattern = re.compile(
        r'op\.create_check_constraint\s*\(\s*'
        rf'"{re.escape(check_name)}"\s*,\s*'
        r'"[^"]*"\s*,\s*'
        r'((?:"[^"]*"\s*)+)',
        re.DOTALL,
    )
    create_match = create_pattern.search(text)
    if create_match:
        # 3번째 인자가 implicit string concat 일 수 있음 — 모든 string 합치기.
        concat_text = create_match.group(1)
        parts = re.findall(r'"([^"]*)"', concat_text)
        return "".join(parts) if parts else None

    return None


def main() -> int:
    failed = False
    # A7 0005 가 ck_collection_job_type 을 drop+recreate 로 갱신 → migration sorted order
    # 의 **마지막** matching clause 만 검사 (이전 버전의 부분 enum 은 무시).
    last_clause: dict[str, tuple[str, str]] = {}  # check_name → (migration_name, clause)
    for migration in sorted(ALEMBIC_DIR.glob("*.py")):
        text = migration.read_text(encoding="utf-8")
        for check in CHECKS:
            clause = _extract_check_clause(text, check["name"])
            if clause is None:
                continue
            last_clause[check["name"]] = (migration.name, clause)
    for check in CHECKS:
        entry = last_clause.get(check["name"])
        if entry is None:
            # 본 시점 alembic 에 CHECK 부재 — 후속 migration 추가 시 활성화.
            continue
        migration_name, clause = entry
        for v in check["enum_values"]:
            if f"'{v}'" not in clause:
                print(
                    f"[FAIL] {check['label']}.{v} 가 {migration_name} "
                    f"의 CHECK {check['name']} (최신 정의) 에 없음.\n"
                    f"  clause: {clause}"
                )
                failed = True
    print("\nCHECK 정합 검사 완료")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
