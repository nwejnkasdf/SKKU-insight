"""자체감사 Critical 4 + Suggested 3 회귀 차단 — fail-to-pass 테스트.

각 fix 가 되돌려지면 본 테스트가 실패하도록 설계. SWE-bench 의 fail-to-pass 개념:
fix 적용 전에는 실패, 적용 후 통과. 본 파일이 새 commit 의 안전 가드.

Critical fixes (commit 57ef185):
- A-1: trace_service.get_trace_detail leaves status=ACTIVE 필터
- A-2: reset_cso_tables 가 dynamic_leaf_topic_cso_topic 도 DELETE
- A-3: cso_service.get_adjacent / get_descendants 가 graph 부재 시 404
- A-4: CSOTopicDetail.parent_topic_id 응답 제거

Suggested:
- B-4: parse_cso_csv 가 utf-8-sig (BOM 처리)
- B-6: router._get_graph dead code 제거
- B-8: seed_broad_interests 가 silent skip 대신 RuntimeError
"""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import networkx as nx
import pytest
from fastapi import HTTPException

from app.topic import cso_importer, cso_service, router, trace_service
from app.topic.schemas import CSOTopicDetail

# ============================================================
# A-1: get_trace_detail leaves 가 status=ACTIVE 필터
# ============================================================


def test_audit_a1_trace_detail_leaves_filter_active_status() -> None:
    """trace_service.get_trace_detail 의 leaf_stmt 가 LeafTopicStatus.ACTIVE 필터 포함.

    fix 전: where (user_id, cso_topic_id.in_(path)) 만 — emerging/stale/merged/archived
    leaf 도 포함되어 list_traces.leaf_count (active 만) 와 불일치.

    fix 후: where (user_id, status=ACTIVE.value, cso_topic_id.in_(path)) — 일관.

    소스 정적 검증 — DB 통합 테스트는 docker 환경 (별도). 핵심: ACTIVE 필터의 존재.
    """
    src = inspect.getsource(trace_service.get_trace_detail)
    # leaf_stmt 안에 status filter 가 있어야 함. 두 패턴 모두 허용:
    # - DynamicLeafTopicORM.status == LeafTopicStatus.ACTIVE.value
    # - DynamicLeafTopicORM.status == "active"
    assert "LeafTopicStatus.ACTIVE" in src or '"active"' in src, (
        "get_trace_detail.leaves 가 status=ACTIVE 필터 누락 (자체감사 A-1 회귀). "
        "list_traces.leaf_count 와 불일치 위험."
    )


def test_audit_a1_list_traces_and_detail_use_same_status_rule() -> None:
    """list_traces.leaf_count 와 get_trace_detail.leaves 가 동일 status 룰.

    두 함수 모두 ACTIVE 만 — fix 후 정합.
    """
    src_list = inspect.getsource(trace_service.list_traces)
    src_detail = inspect.getsource(trace_service.get_trace_detail)
    assert "LeafTopicStatus.ACTIVE" in src_list
    assert "LeafTopicStatus.ACTIVE" in src_detail


# ============================================================
# A-2: reset_cso_tables 가 dynamic_leaf_topic_cso_topic 도 DELETE
# ============================================================


def test_audit_a2_reset_includes_leaf_cso_mapping() -> None:
    """reset_cso_tables 가 dynamic_leaf_topic_cso_topic DELETE 포함.

    fix 전: 3 DELETE (broad_interest → cso_topic_parent → cso_topic). A7 leaf
    매핑 row 채워진 후 ON DELETE RESTRICT FK 위반 → rollback.

    fix 후: 4 DELETE (dynamic_leaf_topic_cso_topic 먼저).
    """
    src = inspect.getsource(cso_importer.reset_cso_tables)
    assert "dynamic_leaf_topic_cso_topic" in src, (
        "reset_cso_tables 가 dynamic_leaf_topic_cso_topic DELETE 누락 "
        "(자체감사 A-2 회귀). RESTRICT FK 위반 가능."
    )


def test_audit_a2_reset_delete_order_correct() -> None:
    """DELETE 순서: dynamic_leaf_topic_cso_topic → broad_interest → cso_topic_parent → cso_topic.

    FK RESTRICT 정합. cso_topic 이 마지막이어야 함.
    """
    src = inspect.getsource(cso_importer.reset_cso_tables)
    # 4 DELETE 의 등장 순서 (str.find)
    indices = {
        "dynamic_leaf_topic_cso_topic": src.find('"DELETE FROM dynamic_leaf_topic_cso_topic"'),
        "broad_interest": src.find('"DELETE FROM broad_interest"'),
        "cso_topic_parent": src.find('"DELETE FROM cso_topic_parent"'),
        "cso_topic": src.find('"DELETE FROM cso_topic"'),
    }
    for table, idx in indices.items():
        assert idx > 0, f"reset_cso_tables 가 {table} DELETE 누락"
    # 순서 검증: dynamic_leaf_topic_cso_topic < broad_interest < cso_topic_parent < cso_topic
    assert (
        indices["dynamic_leaf_topic_cso_topic"]
        < indices["broad_interest"]
        < indices["cso_topic_parent"]
        < indices["cso_topic"]
    ), f"DELETE 순서 잘못됨: {indices}"


# ============================================================
# P2-16 + P2-10: --reset 가드 (dynamic_leaf_topic / user_cso_traversal 행 존재 시)
# ============================================================


def test_audit_p2_16_p2_10_reset_signature_includes_force_orphan() -> None:
    """reset_cso_tables 시그니처에 force_orphan 키워드 인자 (P2-16 + P2-10).

    fix 전: reset_cso_tables(session) 단일 인자 — leaf/traversal 있어도 DELETE 강행
    → leaf 응답 cso_topic_ids=[], traversal.path UUID stale.
    fix 후: force_orphan=False default + count > 0 이면 RuntimeError.
    """
    sig = inspect.signature(cso_importer.reset_cso_tables)
    assert "force_orphan" in sig.parameters, (
        "reset_cso_tables 가 force_orphan 인자 누락 (P2-16 + P2-10 회귀)."
    )
    assert sig.parameters["force_orphan"].default is False, (
        "reset_cso_tables.force_orphan default 가 False 가 아님 — "
        "운영자 명시 없는 reset 이 leaf/traversal orphan 을 강행할 위험."
    )


@pytest.mark.asyncio
async def test_audit_p2_16_p2_10_reset_raises_when_leaf_or_traversal_exists() -> None:
    """leaf 또는 traversal row 가 존재 + force_orphan=False → RuntimeError.

    SELECT count(*) 두 번 호출 — 결과를 AsyncMock 으로 1/0 컨트롤.
    """
    session_mock = AsyncMock()

    # 두 SELECT count(*) 응답 — leaf=1, traversal=0
    leaf_result = MagicMock()
    leaf_result.scalar_one = MagicMock(return_value=1)
    traversal_result = MagicMock()
    traversal_result.scalar_one = MagicMock(return_value=0)
    session_mock.execute = AsyncMock(side_effect=[leaf_result, traversal_result])

    with pytest.raises(RuntimeError) as exc:
        await cso_importer.reset_cso_tables(session_mock, force_orphan=False)
    msg = str(exc.value)
    assert "P2-16" in msg and "P2-10" in msg, (
        "RuntimeError 메시지에 P2-16 / P2-10 표시 누락 — 운영자 디버깅 어려움."
    )
    assert "force-orphan-cso-refs" in msg, (
        "RuntimeError 메시지에 CLI 플래그 명 누락 — 운영자가 우회 경로 모름."
    )

    # 실제 DELETE 가 호출되면 안 됨 (count 2회만)
    assert session_mock.execute.await_count == 2, (
        f"reset 가드 RuntimeError 후에도 DELETE 가 진행됐음 "
        f"(execute call count={session_mock.execute.await_count})."
    )


@pytest.mark.asyncio
async def test_audit_p2_16_p2_10_reset_proceeds_when_force_orphan() -> None:
    """leaf 1행 + force_orphan=True → 정상 진행 (4 DELETE + 1 flush)."""
    session_mock = AsyncMock()
    leaf_result = MagicMock()
    leaf_result.scalar_one = MagicMock(return_value=1)
    traversal_result = MagicMock()
    traversal_result.scalar_one = MagicMock(return_value=0)
    # 6 execute: 2 SELECT count + 4 DELETE. (flush 는 별도 await)
    session_mock.execute = AsyncMock(
        side_effect=[leaf_result, traversal_result] + [MagicMock()] * 4
    )
    session_mock.flush = AsyncMock()

    await cso_importer.reset_cso_tables(session_mock, force_orphan=True)
    assert session_mock.execute.await_count == 6, (
        "force_orphan=True 인데 DELETE 가 진행되지 않음 "
        f"(execute call count={session_mock.execute.await_count}, 기대 6)."
    )
    session_mock.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_p2_16_p2_10_reset_proceeds_when_empty_tables() -> None:
    """leaf=0 + traversal=0 + force_orphan=False (default) → 정상 진행.

    가드가 사용자 경험을 망치지 않는지 확인 — 빈 DB 의 fresh import 는 자유롭게.
    """
    session_mock = AsyncMock()
    zero_result = MagicMock()
    zero_result.scalar_one = MagicMock(return_value=0)
    session_mock.execute = AsyncMock(
        side_effect=[zero_result, zero_result] + [MagicMock()] * 4
    )
    session_mock.flush = AsyncMock()

    await cso_importer.reset_cso_tables(session_mock, force_orphan=False)
    assert session_mock.execute.await_count == 6
    session_mock.flush.assert_awaited_once()


def test_audit_p2_16_p2_10_import_cso_cli_exposes_force_orphan_flag() -> None:
    """scripts/import_cso.py CLI 가 --force-orphan-cso-refs 플래그 노출.

    `_parse_args` 안에 본 플래그 정의 + dest=force_orphan_cso_refs.
    """
    from scripts import import_cso as import_cso_script

    src = inspect.getsource(import_cso_script._parse_args)
    assert "--force-orphan-cso-refs" in src, (
        "scripts/import_cso.py 가 --force-orphan-cso-refs 플래그 누락 "
        "(P2-16 + P2-10 회귀). 운영자가 가드 우회 못 함."
    )
    assert "force_orphan_cso_refs" in src, (
        "scripts/import_cso.py 의 dest 가 force_orphan_cso_refs 가 아님 "
        "(argparse default dest 가 force-orphan-cso-refs 가 아니므로 명시 필요)."
    )


# ============================================================
# A-3: cso_service.get_adjacent / get_descendants 가 graph 부재 시 404
# ============================================================


@pytest.mark.asyncio
async def test_audit_a3_get_adjacent_404_when_graph_missing() -> None:
    """빈 그래프 + 임의 UUID → HTTPException 404 (DB 조회 없이 즉시).

    fix 전: graph 없으면 DB count 조회 → DB 에 있으면 fall through (find_adjacent
    가 빈 list → 200 OK + topics=[]). 의도 불일치.

    fix 후: graph 부재 시 무조건 404. DB 조회 안 함.
    """
    empty_graph: nx.DiGraph = nx.DiGraph()
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock()  # 호출되면 안 됨

    with pytest.raises(HTTPException) as exc:
        await cso_service.get_adjacent(db_mock, empty_graph, uuid4(), hops=1)
    assert exc.value.status_code == 404
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "topic.not_found"
    # DB 조회가 일어나지 않았는지 검증 (fix 전에는 호출됨)
    db_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_audit_a3_get_descendants_404_when_graph_missing() -> None:
    """동일 fix 적용 — get_descendants 도 graph 부재 시 즉시 404."""
    empty_graph: nx.DiGraph = nx.DiGraph()
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await cso_service.get_descendants(db_mock, empty_graph, uuid4())
    assert exc.value.status_code == 404
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "topic.not_found"
    db_mock.execute.assert_not_called()


# ============================================================
# A-4: CSOTopicDetail.parent_topic_id 응답 필드 제거
# ============================================================


def test_audit_a4_cso_topic_detail_excludes_parent_topic_id() -> None:
    """CSOTopicDetail Pydantic 모델에 `parent_topic_id` 필드 없음.

    fix 전: parent_topic_id: UUID | None = None 노출 (deprecated 단일 FK).
    fix 후: 제거. parents list (cso_topic_parent M:N SOR) 만.
    """
    fields = CSOTopicDetail.model_fields.keys()
    assert "parent_topic_id" not in fields, (
        "CSOTopicDetail 에 deprecated parent_topic_id 필드 노출 (자체감사 A-4 회귀). "
        "결정 18 (cso_topic_parent SOR) 위반."
    )
    # 단, parents list 는 유지
    assert "parents" in fields


def test_audit_a4_cso_topic_detail_required_fields() -> None:
    """CSOTopicDetail 가 필수 필드 모두 보유: cso_topic_id / label / uri / parents / children_count."""
    fields = set(CSOTopicDetail.model_fields.keys())
    expected = {"cso_topic_id", "label", "uri", "parents", "children_count"}
    assert expected.issubset(fields), f"missing: {expected - fields}"
    # parent_topic_id (제거됨) 외 다른 필드 없음 (확장 시 본 테스트 갱신)
    assert fields == expected, f"unexpected extra fields: {fields - expected}"


# ============================================================
# B-4: parse_cso_csv 가 utf-8-sig (BOM 처리)
# ============================================================


def test_audit_b4_parse_cso_csv_handles_utf8_bom(tmp_path: Path) -> None:
    """CSO CSV 가 BOM 으로 시작해도 첫 URI 정상 파싱.

    fix 전: encoding="utf-8" → 첫 줄 첫 필드에 \\ufeff 부착 → URI 매칭 실패.
    fix 후: encoding="utf-8-sig" → BOM 자동 스트립.
    """
    bom = "﻿"
    csv_content = (
        bom
        + '<http://example.org/ai>,<http://www.w3.org/2000/01/rdf-schema#label>,"Artificial Intelligence"\n'
    )
    csv_path = tmp_path / "bom_cso.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    # 실제 BOM 이 파일에 들어갔는지 확인
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "테스트 fixture 가 BOM 시작해야 함"

    topics = cso_importer.parse_cso_csv(csv_path)
    # BOM 잘못 처리 시 "﻿<http://...>" 또는 "ufeffhttp://..." 같은 키 등장
    assert "http://example.org/ai" in topics, (
        f"BOM 처리 회귀 (자체감사 B-4): keys={list(topics.keys())}"
    )
    assert topics["http://example.org/ai"]["label"] == "Artificial Intelligence"


# ============================================================
# B-6: router._get_graph dead code 제거
# ============================================================


def test_audit_b6_no_get_graph_dead_code() -> None:
    """router 모듈에 `_get_graph` 함수 없음 (dead code 제거 후)."""
    assert not hasattr(router, "_get_graph"), (
        "router._get_graph dead code 재등장 (자체감사 B-6 회귀)."
    )


# ============================================================
# B-8: seed_broad_interests 가 silent skip 대신 RuntimeError
# ============================================================


@pytest.mark.asyncio
async def test_audit_b8_seed_broad_interests_raises_on_mismatch(
    tmp_path: Path,
) -> None:
    """seed_topic_label 이 cso_topic 에 없으면 RuntimeError.

    fix 전: silent skip + warning → broad_interest 12 행 미만 (UI 의 12 카드 누락).
    fix 후: RuntimeError — 시드 무결성 보장.
    """
    # 잘못된 seed_topic_label (CSO 에 존재하지 않는 라벨)
    toml_content = """
[[broad_interest]]
name = "Unknown"
description_ko = "test"
cso_cluster_label = "Unknown"
seed_topic_label = "Nonexistent CSO Label That Does Not Match Anything"
display_order = 0
"""
    toml_path = tmp_path / "broad_interests_bad.toml"
    toml_path.write_text(toml_content, encoding="utf-8")

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock()
    session_mock.flush = AsyncMock()
    uri_to_id: dict[str, object] = {}
    topics: dict[str, dict[str, object]] = {
        "uri://ai": {"label": "Artificial Intelligence", "parent_uris": [], "equivalents": []},
    }

    with pytest.raises(RuntimeError, match="BroadInterest 시드 누락"):
        await cso_importer.seed_broad_interests(
            session_mock, toml_path, uri_to_id, topics  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_audit_b8_seed_broad_interests_succeeds_when_all_match(
    tmp_path: Path,
) -> None:
    """모든 seed_topic_label 매칭 + uri_to_id 채워짐 → 정상 INSERT."""
    toml_content = """
[[broad_interest]]
name = "AI"
description_ko = "인공지능"
cso_cluster_label = "AI"
seed_topic_label = "Artificial Intelligence"
display_order = 0
"""
    toml_path = tmp_path / "broad_interests_ok.toml"
    toml_path.write_text(toml_content, encoding="utf-8")

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock()
    session_mock.flush = AsyncMock()
    seed_id = uuid4()
    uri_to_id = {"uri://ai": seed_id}
    topics: dict[str, dict[str, object]] = {
        "uri://ai": {"label": "Artificial Intelligence", "parent_uris": [], "equivalents": []},
    }

    inserted = await cso_importer.seed_broad_interests(
        session_mock, toml_path, uri_to_id, topics
    )
    assert inserted == 1
    # session.execute 가 1번 호출됨 (1 entry)
    assert session_mock.execute.call_count == 1


# ============================================================
# Codex Critical: --reset split-transaction → 단일 transaction
# ============================================================


def test_audit_codex_critical_single_transaction_wraps_reset_and_seed() -> None:
    """import_cso.py 의 `_main` 이 reset + insert + seed 를 `session.begin()` 단일 transaction 으로 래핑.

    fix 전: reset 후 즉시 commit + insert/seed 별도 commit → seed 가 RuntimeError
    던지면 reset 만 완료, DB empty 잔존.

    fix 후: session.begin() context manager 가 RuntimeError 시 reset 까지 rollback.
    소스 패턴 검증 — 모듈 직접 import 대신 파일 read (backend/ 패키지 경로 회피).
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    import_cso_path = repo_root / "scripts" / "import_cso.py"
    src = import_cso_path.read_text(encoding="utf-8")
    # session.begin() context manager 가 reset/insert/seed 를 모두 감싸야 함
    assert "session.begin()" in src, (
        "import_cso._main 가 session.begin() 으로 reset+insert+seed 단일 transaction "
        "래핑 누락 (Codex Critical 회귀). reset 후 RuntimeError 시 빈 DB 잔존 위험."
    )
    # 별도 `await session.commit()` 호출 안 함 (begin() 가 자동 commit)
    explicit_commit_count = src.count("await session.commit()")
    assert explicit_commit_count == 0, (
        f"import_cso._main 에 명시 commit {explicit_commit_count}회 — session.begin() "
        "패턴과 충돌. split-transaction 위험 (Codex Critical 회귀)."
    )


# ============================================================
# Codex B-1: download atomic rename + 최소 size 검증
# ============================================================


def test_audit_codex_b1_download_uses_atomic_tmp_rename() -> None:
    """download_cso 가 `.tmp` 로 받은 후 atomic rename 패턴 사용.

    fix 전: target 에 직접 write — partial 다운로드가 다음 실행에 cache hit.
    fix 후: tmp.replace(target) atomic + size 검증 + partial 정리.
    """
    src = inspect.getsource(cso_importer.download_cso)
    assert ".tmp" in src and "replace(target)" in src, (
        "download_cso 가 atomic .tmp rename 패턴 누락 (Codex B-1 회귀). "
        "partial download cache hit 위험."
    )
    # 최소 size 검증 (10 KB)
    assert "10_000" in src or "10000" in src, (
        "download_cso 가 최소 size 검증 누락 (Codex B-1 회귀)."
    )


# ============================================================
# Codex B-2: cursor decoder TypeError 처리
# ============================================================


def test_audit_codex_b2_leaf_cursor_decoder_handles_typeerror() -> None:
    """leaf_service._decode_cursor 가 list/잘못된 type payload → 400.

    fix 전: dict 가정 → list 인 payload 면 KeyError 아닌 TypeError 발생 → except 불일치 → 500.
    fix 후: isinstance(data, dict) check + TypeError except → 400.
    """
    import base64
    import json as json_mod

    from app.topic import leaf_service

    # cursor 가 list (dict 아님) — 잘못된 payload
    bad_payload = base64.urlsafe_b64encode(json_mod.dumps([1, 2]).encode()).decode().rstrip("=")
    with pytest.raises(HTTPException) as exc:
        leaf_service._decode_cursor(bad_payload)
    assert exc.value.status_code == 400


def test_audit_codex_b2_trace_cursor_decoder_handles_typeerror() -> None:
    """trace_service._decode_cursor 도 동일 패턴."""
    import base64
    import json as json_mod

    from app.topic import trace_service as ts

    bad_payload = base64.urlsafe_b64encode(json_mod.dumps([1, 2]).encode()).decode().rstrip("=")
    with pytest.raises(HTTPException) as exc:
        ts._decode_cursor(bad_payload)
    assert exc.value.status_code == 400


# ============================================================
# Codex B-3: cluster cache schema mismatch fallback
# ============================================================


@pytest.mark.asyncio
async def test_audit_codex_b3_cluster_cache_validation_error_invalidates() -> None:
    """get_clusters 가 cache 의 schema mismatch (ValidationError) 시 DEL + DB fallback.

    fix 전: cache 에 stale schema 저장 시 model_validate ValidationError → 500.
    fix 후: ValidationError catch → invalidate_cluster_cache 호출 → DB 조회 fallback.
    """
    src = inspect.getsource(cso_service.get_clusters)
    assert "ValidationError" in src, (
        "get_clusters 가 cache ValidationError catch 누락 (Codex B-3 회귀). 500 위험."
    )
    assert "invalidate_cluster_cache" in src, (
        "get_clusters 가 ValidationError 후 cache invalidate 누락 (Codex B-3 회귀)."
    )


# ============================================================
# Codex B-4: clusters response 12개 보장 fail-fast
# ============================================================


def test_audit_codex_b4_clusters_response_enforces_12() -> None:
    """get_clusters 가 len(clusters) != 12 시 503 fail-fast.

    fix 전: 0 또는 부분 행 (1-11) 도 200 + 빈 list 반환. 운영자 인지 불가.
    fix 후: 12 아니면 503 topic.linkage_error.
    """
    src = inspect.getsource(cso_service.get_clusters)
    # 503 fail-fast 패턴 검증
    assert "503" in src or "HTTP_503" in src, (
        "get_clusters 가 12 cluster 보장 503 fail-fast 누락 (Codex B-4 회귀)."
    )
    assert "12" in src, "get_clusters 가 12 cluster 보장 검증 누락 (Codex B-4 회귀)."


# ============================================================
# Codex 2nd Critical A-1: cache hit 경로 12 검증 bypass 차단 (DYNAMIC)
# ============================================================


@pytest.mark.asyncio
async def test_audit_codex_2nd_critical_cache_hit_11_clusters_invalidated() -> None:
    """Redis 에 11개 cluster cache 가 있어도 200 반환 안 함 — invalidate + DB fallback.

    fix 전: cache hit 시 ClustersResponse(...) 즉시 반환 → 503 우회.
    fix 후: cache hit 후 len(parsed) != 12 → invalidate + DB fallback (DB 단계 503 guard 가 최종 권위).

    검증 방식: get_cluster_cache 가 11 entry 반환하도록 mock + DB 도 11 행 반환 →
    invalidate_cluster_cache 호출 + 503 응답.
    """
    from unittest.mock import patch
    from uuid import uuid4

    # mock cached payload (11개 — 12 미달)
    cached_11 = [
        {
            "cso_topic_id": str(uuid4()),
            "label": f"cluster_{i}",
            "description_ko": "test",
            "document_count": 0,
        }
        for i in range(11)
    ]
    redis_mock = AsyncMock()
    # invalidate_cluster_cache 가 호출되었는지 검증
    invalidate_called = AsyncMock()

    # DB mock — 11 행 반환 (DB 단계도 비정상 → 503 raise)
    db_mock = AsyncMock()
    db_rows: list[MagicMock] = []
    for i in range(11):
        row = MagicMock()
        row.cso_seed_topic_id = uuid4()
        row.name = f"cluster_{i}"
        row.description = "test"
        db_rows.append(row)
    db_result = MagicMock()
    db_result.__iter__ = MagicMock(return_value=iter(db_rows))
    db_mock.execute = AsyncMock(return_value=db_result)

    with patch(
        "app.topic.cache.get_cluster_cache",
        new=AsyncMock(return_value=cached_11),
    ), patch(
        "app.topic.cache.invalidate_cluster_cache",
        new=invalidate_called,
    ):
        with pytest.raises(HTTPException) as exc:
            await cso_service.get_clusters(db_mock, redis_mock)

    assert exc.value.status_code == 503, (
        "cache hit 의 11/12 payload 가 12 검증 bypass — Codex 2nd Critical 회귀. "
        "503 fail-fast 가 cache hit 경로에도 적용되어야 함."
    )
    # cache invalidate 호출되었는지 확인
    invalidate_called.assert_called_once_with(redis_mock)


@pytest.mark.asyncio
async def test_audit_codex_2nd_critical_cache_hit_valid_12_returns_200() -> None:
    """Redis 에 정확 12개 cluster cache 가 있으면 정상 ClustersResponse 200 반환.

    A-1 fix 가 정상 흐름 (12개 cache) 까지 막지 않음을 보장.
    """
    from unittest.mock import patch
    from uuid import uuid4

    cached_12 = [
        {
            "cso_topic_id": str(uuid4()),
            "label": f"cluster_{i}",
            "description_ko": "test",
            "document_count": 0,
        }
        for i in range(12)
    ]
    redis_mock = AsyncMock()
    db_mock = AsyncMock()

    with patch(
        "app.topic.cache.get_cluster_cache",
        new=AsyncMock(return_value=cached_12),
    ):
        result = await cso_service.get_clusters(db_mock, redis_mock)

    assert len(result.clusters) == 12
    # DB 조회 일어나지 않음
    db_mock.execute.assert_not_called()
