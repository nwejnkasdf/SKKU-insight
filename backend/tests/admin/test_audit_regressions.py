"""C-61 회귀 가드 — admin debug console.

정적 source inspection 만 사용 — conftest 의 무거운 fixture 의존 없이 P2-25 회피.

검증 대상:
1. 신규 endpoint 12개 모두 SUPER gate (`require_admin_role(AdminRole.SUPER)`) 또는 `auth/me` 처럼 `get_current_admin` 만 검증.
2. 신규 schema 11종 export.
3. 신규 service 모듈 5+7 함수 export.
4. 신규 worker job 등록 (`app.worker.jobs.__init__` import).
5. `RedisKey.simulate_status` 정의.
6. simulate worker 의 weekly auto-chain 룰 (`active_day % 7 == 0`) 명시.
7. force_archive_trace 가 `TraversalStatus.ARCHIVED.value` 사용 (path.pop 없음 — 의도 정합).
"""
from __future__ import annotations

import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # backend/


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_c61_schemas_exported():
    from app.admin import schemas

    required = {
        "AdminTraceView",
        "AdminLeafView",
        "AdminRecommendationView",
        "SimulateRequest",
        "SimulateAcceptedResponse",
        "SimulateStatusResponse",
        "ForceActionRequest",
        "CleanupPseudoResponse",
        "SystemConfigItem",
        "SystemConfigListResponse",
        "SystemConfigUpdateRequest",
    }
    missing = required - set(schemas.__all__)
    assert not missing, f"admin/schemas.py __all__ 누락: {missing}"


def test_c61_insights_service_exports():
    from app.admin import insights_service

    expected = {
        "get_admin_me",
        "get_admin_traces",
        "get_admin_leaves",
        "get_admin_recommendations",
        "get_admin_interest_state",
    }
    missing = expected - set(insights_service.__all__)
    assert not missing, f"insights_service __all__ 누락: {missing}"


def test_c61_actions_service_exports():
    from app.admin import actions_service

    expected = {
        "force_archive_leaf",
        "force_archive_trace",
        "cleanup_pseudo_recos",
        "enqueue_simulate",
        "get_simulate_status",
        "list_system_config",
        "update_system_config",
    }
    missing = expected - set(actions_service.__all__)
    assert not missing, f"actions_service __all__ 누락: {missing}"


def test_c61_router_super_gate_on_insights_and_actions():
    """router.py 안 신규 endpoint 가 require_admin_role(AdminRole.SUPER) gate 사용."""
    src = _read("app/admin/router.py")
    # 신규 인사이트 endpoint 5개 — interest-state 본문화 + traces / leaves / recommendations + simulate / simulate/status
    super_required_paths = [
        '"/users/{user_id}/traces"',
        '"/users/{user_id}/leaves"',
        '"/users/{user_id}/recommendations"',
        '"/users/{user_id}/interest-state"',
        '"/users/{user_id}/leaves/{leaf_id}/archive"',
        '"/users/{user_id}/traces/{trace_id}/retract"',
        '"/users/{user_id}/recommendations/cleanup-pseudo"',
        '"/users/{user_id}/simulate"',
        '"/users/{user_id}/simulate/status"',
        '"/system-config"',
        '"/system-config/{key}"',
    ]
    for path in super_required_paths:
        assert path in src, f"router 에 endpoint path 누락: {path}"
    # SUPER gate count — 11개 endpoint 가 require_admin_role(AdminRole.SUPER) Depends 사용.
    super_gate_count = src.count("require_admin_role(AdminRole.SUPER)")
    assert super_gate_count >= 11, (
        f"router 안 SUPER gate Depends 가 {super_gate_count} 회 — 11+ 기대"
    )


def test_c61_auth_me_endpoint_no_role_gate():
    """/admin/auth/me 는 SUPER gate 없이 get_current_admin 만 — 모든 admin role 호출 가능."""
    src = _read("app/admin/router.py")
    assert '"/auth/me"' in src
    # me endpoint 본문 부근 (소스 50줄 내) 에 require_admin_role 없음.
    me_idx = src.index('"/auth/me"')
    section = src[me_idx : me_idx + 600]
    assert "require_admin_role" not in section, (
        "auth/me 가 role gate 사용 — 의도 위배 (SPA 가 role 분기용)"
    )


def test_c61_simulate_status_redis_key_defined():
    """RedisKey.simulate_status 정의 + simulate 영역 SOR."""
    from app.contracts import RedisKey
    from uuid import uuid4

    uid = uuid4()
    key = RedisKey.simulate_status(uid)
    assert key.startswith("simulate:") and key.endswith(":status"), key
    assert str(uid) in key


def test_c61_simulate_worker_weekly_auto_chain():
    """simulate_user_day_job 안 `% 7 == 0` 룰 명시 — weekly auto-chain SOR."""
    src = _read("app/worker/jobs/simulate_user_day_job.py")
    assert "% 7 == 0" in src, "weekly auto-chain 룰 (active_day % 7 == 0) 누락"
    # next_day / full_day / weekly mode 분기 모두 본문 안.
    assert '"weekly"' in src
    assert '"full_day"' in src
    # subprocess 패턴 — scripts/simulate_user_day.py 호출
    assert "scripts.simulate_user_day" in src
    # collection 호출은 full_day 전용
    assert '"collection"' in src


def test_c61_simulate_worker_registered_in_jobs_init():
    """app/worker/jobs/__init__.py 가 simulate_user_day_job import — RQ unpickle 정합."""
    from app.worker import jobs

    assert "simulate_user_day_job" in jobs.__all__
    assert hasattr(jobs.simulate_user_day_job, "simulate_user_day_job")


def test_c61_force_archive_trace_no_path_pop():
    """force_archive_trace 가 path.pop / array_remove 사용 X — archive 통일 명세 정합."""
    src = _read("app/admin/actions_service.py")
    # force_archive_trace 본문 부근.
    fn_idx = src.index("async def force_archive_trace(")
    end_idx = src.index("async def cleanup_pseudo_recos(")
    section = src[fn_idx:end_idx]
    assert "TraversalStatus.ARCHIVED.value" in section
    assert "array_remove" not in section, (
        "force_archive_trace 가 path.pop 사용 — 명세 정합 위반 (archive 통일)"
    )
    assert "execute_retract" not in section, (
        "force_archive_trace 가 execute_retract 호출 — 본 라운드 의도 (단순 archive) 위반"
    )


def test_c61_actions_invalidate_recommendation_cache():
    """force_archive_leaf / force_archive_trace 모두 recommendation cache invalidate."""
    src = _read("app/admin/actions_service.py")
    # 두 함수 모두 RedisKey.recommendation_cache 호출.
    count = src.count("RedisKey.recommendation_cache")
    assert count >= 2, f"force-archive 2종 모두 cache invalidate 필요, found {count}"


def test_c61_update_system_config_cache_invalidate():
    """update_system_config 가 Redis system_config_cache DEL."""
    src = _read("app/admin/actions_service.py")
    fn_idx = src.index("async def update_system_config(")
    section = src[fn_idx:]
    assert "RedisKey.system_config_cache" in section
    assert ".delete(" in section


def test_c61_insights_service_signature_compat():
    """insights_service 의 5 함수가 (db, user_id, ...) 일관 시그니처."""
    from app.admin import insights_service

    sigs = {
        "get_admin_traces": ["db", "user_id"],
        "get_admin_leaves": ["db", "user_id"],
        "get_admin_recommendations": ["db", "user_id"],
        # interest_state 만 redis 필요 (bucket_for 가 interest_params 사용)
        "get_admin_interest_state": ["db", "redis", "user_id"],
    }
    for name, expected_params in sigs.items():
        fn = getattr(insights_service, name)
        params = list(inspect.signature(fn).parameters.keys())
        for p in expected_params:
            assert p in params, f"{name} 시그니처에 {p} 없음 (got {params})"
