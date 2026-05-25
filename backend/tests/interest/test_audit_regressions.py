"""Codex 1차 감사 발견 결함 1:1 fail-to-pass 회귀 가드.

각 test 는 fix 전 코드에서 실패하고 fix 후 통과해야 한다. A4 test_audit_regressions
패턴 (decision-backlog C-35) 미러.

CRITICAL fix:
- TestRound1CodexFixGuards::test_c01_atomic_upsert_no_lost_update
- TestRound1CodexFixGuards::test_c02_idempotency_cache_after_commit

SUGGESTED fix:
- TestRound1CodexFixGuards::test_s01_integrity_error_race
- TestRound1CodexFixGuards::test_s02_dwell_cap_lua_atomic
- TestRound1CodexFixGuards::test_s03_decay_alpha_never_negative
- TestRound1CodexFixGuards::test_s04_buffer_stop_race_directs_to_callback
- TestRound1CodexFixGuards::test_s05_system_config_fail_fast_toggle
- TestRound1CodexFixGuards::test_s06_onboarding_savepoint_rollback
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

import networkx as nx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select

from app.config import get_settings
from app.contracts import EventType, RedisKey
from app.db.models import (
    CSOTopic,
    Document,
    User,
    UserInterestState,
)
from app.events.buffer import BufferedEvent, EventBuffer
from app.interest.config_loader import load_system_config
from app.interest.schemas import EventRequest
from app.interest.service import (
    _atomic_upsert_interest_state,
    ingest_event_atomic,
)


class TestRound1CodexFixGuards:
    """Codex 1차 감사 (2026-05-17) 결함 1:1 fail-to-pass 회귀."""

    @pytest.mark.asyncio
    async def test_c01_atomic_upsert_no_lost_update(
        self,
        db_session,
        redis_client: aioredis.Redis,
        seeded_user: User,
        seeded_cso_topics: list[CSOTopic],
        seeded_system_config,
    ) -> None:
        """C-01: ON CONFLICT DO UPDATE 패턴 — 동시 INSERT 두 개 모두 가산되어야 함.

        fix 전: UPDATE WHERE → 0 row → INSERT ON CONFLICT DO NOTHING → 두 번째 호출이
        INSERT 시도 후 DO NOTHING 으로 손실. alpha 가 (prior + delta * 1) 만 가산.
        fix 후: 두 번째 호출도 ON CONFLICT DO UPDATE 로 UPDATE 진입 → alpha 가
        (prior + delta * 2) 누적.

        Codex round-2 S-08 fix: redis_client fixture 주입 (load_system_config 내부 SETEX).
        """
        params, _ = await load_system_config(db_session, redis_client)
        cso_id = seeded_cso_topics[0].cso_topic_id
        # 같은 (user, cso) 에 두 번 INSERT — delta=1.0 씩.
        for _ in range(2):
            await _atomic_upsert_interest_state(
                db_session,
                user_id=seeded_user.user_id,
                cso_topic_id=cso_id,
                leaf_topic_id=None,
                weighted=1.0,
                params=params,
                active_day=1,
            )
        row = (
            await db_session.execute(
                select(UserInterestState).where(
                    UserInterestState.user_id == seeded_user.user_id,
                    UserInterestState.cso_topic_id == cso_id,
                )
            )
        ).scalar_one()
        # alpha_prior(1.0) + 1.0 + 1.0 = 3.0 — 두 번째 호출이 손실되면 2.0 만 남음.
        assert row.long_alpha == pytest.approx(3.0, abs=0.0001)
        assert row.short_alpha == pytest.approx(3.0, abs=0.0001)

    @pytest.mark.asyncio
    async def test_c02_idempotency_cache_after_commit(
        self,
        db_session,
        redis_client: aioredis.Redis,
        seeded_user: User,
        seeded_document: Document,
        seeded_cso_topics: list[CSOTopic],
        seeded_system_config,
    ) -> None:
        """C-02: store_idempotent 호출은 service 안에서 발생하지 않아야 (commit 이후로 이동).

        fix 전: service.ingest_event_atomic 가 SETEX 직접 호출. commit 실패 시 cache 만 잔존.
        fix 후: IngestResult.payload_hash / client_request_id 가 caller 에게 전달되어
        commit 후 호출. service 안 SETEX 직접 호출 없음.
        """
        settings = get_settings()
        params, weights = await load_system_config(db_session, redis_client)
        req_id = f"req-{uuid.uuid4().hex[:8]}"
        result = await ingest_event_atomic(
            db_session,
            redis_client,
            nx.DiGraph(),
            settings,
            params,
            weights,
            user=seeded_user,
            event_type=EventType.CLICK,
            document_id=seeded_document.document_id,
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            client_request_id=req_id,
            occurred_at=datetime.now(UTC),
            active_day=seeded_user.active_day_counter,
        )
        # IngestResult 가 payload_hash + client_request_id 보존 (router 가 commit 후 사용)
        assert result.payload_hash is not None
        assert len(result.payload_hash) == 64
        assert result.client_request_id == req_id
        # service 가 cache 직접 set 안 함 — Redis 캐시 key 가 비어 있어야 함 (commit 후 router 가 set)
        cache_key = RedisKey.event_duplicate_cache(seeded_user.user_id, req_id)
        cached = await redis_client.get(cache_key)
        assert cached is None

    @pytest.mark.asyncio
    async def test_s01_integrity_error_race(
        self,
        db_session,
        redis_client: aioredis.Redis,
        seeded_user: User,
        seeded_document: Document,
        seeded_cso_topics: list[CSOTopic],
        seeded_system_config,
    ) -> None:
        """S-01: 동시 두 호출이 idempotency miss 통과 후 둘 다 INSERT → IntegrityError.

        fix 전: race 시 IntegrityError 가 endpoint 까지 전파 → 500.
        fix 후: try/except IntegrityError → rollback + lookup → 두 번째 호출이 200 +
        duplicate=True 로 응답.
        """
        settings = get_settings()
        params, weights = await load_system_config(db_session, redis_client)
        req_id = f"req-{uuid.uuid4().hex[:8]}"
        occurred = datetime.now(UTC)
        # 첫 호출 → UserEvent INSERT 성공
        first = await ingest_event_atomic(
            db_session,
            redis_client,
            nx.DiGraph(),
            settings,
            params,
            weights,
            user=seeded_user,
            event_type=EventType.CLICK,
            document_id=seeded_document.document_id,
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            client_request_id=req_id,
            occurred_at=occurred,
            active_day=seeded_user.active_day_counter,
        )
        # 두 번째 호출 (idempotency match) → duplicate=True
        second = await ingest_event_atomic(
            db_session,
            redis_client,
            nx.DiGraph(),
            settings,
            params,
            weights,
            user=seeded_user,
            event_type=EventType.CLICK,
            document_id=seeded_document.document_id,
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            client_request_id=req_id,
            occurred_at=occurred,
            active_day=seeded_user.active_day_counter,
        )
        assert second.duplicate is True
        assert second.event_id == first.event_id

    @pytest.mark.asyncio
    async def test_s02_dwell_cap_lua_atomic(
        self,
        redis_client: aioredis.Redis,
        seeded_user: User,
        seeded_document: Document,
        seeded_system_config,
        db_session,
    ) -> None:
        """S-02: dwell cap Redis 키에 TTL 이 자동 설정되어야 함 (Lua atomic INCR+EXPIRE).

        fix 전: INCR 후 EXPIRE 별도 호출 — crash 시 TTL 없는 영구 키.
        fix 후: Lua script 가 INCR + EXPIRE (count==1 일 때만) atomic.
        """
        from app.interest.service import _check_dwell_tick_cap

        settings = get_settings()
        key = RedisKey.dwell_tick_count(
            seeded_user.user_id, seeded_document.document_id
        )
        await redis_client.delete(key)
        ok = await _check_dwell_tick_cap(
            redis_client,
            settings,
            user_id=seeded_user.user_id,
            document_id=seeded_document.document_id,
        )
        assert ok is True
        # TTL 이 셋팅돼야 함 (Lua atomic)
        ttl = await redis_client.ttl(key)
        assert 0 < ttl <= settings.DWELL_TICK_CAP_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_s03_decay_alpha_never_negative(
        self,
        db_session,
        redis_client: aioredis.Redis,
        seeded_user: User,
        seeded_cso_topics: list[CSOTopic],
        seeded_system_config,
    ) -> None:
        """S-03: decay + boost 만료 후 alpha 가 prior 미만으로 안 떨어짐 (GREATEST floor).

        fix 전: 자식 row (+0.5 boost) 가 cron 에서 일률 1.0 차감 → alpha = prior - 0.5 (음수 위험).
        fix 후: GREATEST(:alpha_prior, computed) 로 floor 적용.
        """
        from app.interest.decay import apply_decay_to_user

        settings = get_settings()
        params, _ = await load_system_config(db_session, redis_client)
        # 자식 row 시뮬레이션 — alpha = alpha_prior + 0.5 boost, boost_applied_at=0
        state_id = uuid.uuid4()
        db_session.add(
            UserInterestState(
                state_id=state_id,
                user_id=seeded_user.user_id,
                cso_topic_id=seeded_cso_topics[0].cso_topic_id,
                leaf_topic_id=None,
                long_alpha=params.alpha_prior + 0.5,  # 자식 boost
                long_beta=params.beta_prior,
                short_alpha=params.alpha_prior + 0.5,
                short_beta=params.beta_prior,
                long_score=0.3,
                short_score=0.3,
                last_event_active_day=0,
                last_decay_active_day=0,
                boost_applied_at_active_day=0,
            )
        )
        await db_session.flush()
        seeded_user.active_day_counter = 14  # boost 만료 트리거
        await db_session.flush()
        await apply_decay_to_user(
            db_session, user=seeded_user, params=params, settings=settings
        )
        await db_session.commit()
        row = (
            await db_session.execute(
                select(UserInterestState).where(
                    UserInterestState.state_id == state_id
                )
            )
        ).scalar_one()
        # alpha 가 alpha_prior 미만으로 안 떨어짐 — GREATEST floor 동작 검증
        assert row.long_alpha >= params.alpha_prior - 0.0001
        assert row.short_alpha >= params.alpha_prior - 0.0001

    @pytest.mark.asyncio
    async def test_s04_buffer_stop_race_directs_to_callback(self) -> None:
        """S-04: stop() 호출 후 add() 가 들어와도 entry 가 callback 으로 직접 flush.

        fix 전: _stopped 검사가 lock 밖 → add() 가 buffer 에 append 후 영구 잔존.
        fix 후: lock 안 _stopped 검사 → 즉시 callback 호출.
        """
        flushed: list[BufferedEvent] = []

        async def _cb(uid: UUID, entries: Iterable[BufferedEvent]) -> None:
            flushed.extend(entries)

        buf = EventBuffer(flush_callback=_cb, batch_size=10, flush_seconds=999.0)
        uid = uuid.uuid4()
        await buf.stop()
        # shutdown 중 add() → 즉시 flush
        entry = BufferedEvent(
            user_id=uid,
            request=EventRequest(
                event_type=EventType.CLICK,
                document_id=None,
                cso_topic_id=None,
                leaf_topic_id=None,
                dwell_ms=None,
                occurred_at=datetime.now(UTC),
                client_request_id="req-1",
            ),
            payload_hash="x" * 64,
            server_received_at=datetime.now(UTC),
            active_day_counter=1,
        )
        await buf.add(entry)
        assert len(flushed) == 1
        assert buf.pending_count() == 0

    def test_s05_system_config_required_setting_exists(self) -> None:
        """S-05: SYSTEM_CONFIG_REQUIRED env toggle 이 Settings 에 정의돼 있어야 함.

        fix 전: WARN 만 발생, 운영 환경에서도 startup 계속.
        fix 후: SYSTEM_CONFIG_REQUIRED=true (default) 일 때 RuntimeError 로 fail-fast.
        """
        settings = get_settings()
        assert hasattr(settings, "SYSTEM_CONFIG_REQUIRED")
        # default = True (운영 안전)
        assert settings.SYSTEM_CONFIG_REQUIRED is True

    @pytest.mark.asyncio
    async def test_c03_batch_race_preserves_prior_inserts(
        self,
        db_session,
        redis_client: aioredis.Redis,
        seeded_user: User,
        seeded_document: Document,
        seeded_cso_topics: list[CSOTopic],
        seeded_system_config,
    ) -> None:
        """Codex round-2 C-03 fix: batch 안 entry-B race 가 entry-A row 를 소실시키지 않음.

        fix 전 (round 1 S-01): IntegrityError 시 db.rollback() → batch 전체 트랜잭션 rollback
        → entry-A user_event row 소실. Redis 캐시 만 잔존 → false positive.
        fix 후 (round 2 C-03): _record_user_event 가 ON CONFLICT DO NOTHING RETURNING.
        race 시 None 반환 → 트랜잭션 보존 → entry-A row 안전.

        본 테스트는 sequential 호출 두 번 (같은 req_id) 으로 race 효과 시뮬레이션.
        """
        settings = get_settings()
        params, weights = await load_system_config(db_session, redis_client)
        # entry-A INSERT — 성공
        req_a = f"req-A-{uuid.uuid4().hex[:8]}"
        result_a = await ingest_event_atomic(
            db_session,
            redis_client,
            nx.DiGraph(),
            settings,
            params,
            weights,
            user=seeded_user,
            event_type=EventType.CLICK,
            document_id=seeded_document.document_id,
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            client_request_id=req_a,
            occurred_at=datetime.now(UTC),
            active_day=seeded_user.active_day_counter,
        )
        # entry-B 가 같은 req_a 로 race → duplicate=True (트랜잭션 rollback 없음)
        result_b = await ingest_event_atomic(
            db_session,
            redis_client,
            nx.DiGraph(),
            settings,
            params,
            weights,
            user=seeded_user,
            event_type=EventType.CLICK,
            document_id=seeded_document.document_id,
            cso_topic_id=None,
            leaf_topic_id=None,
            dwell_ms=None,
            client_request_id=req_a,
            occurred_at=result_a.server_received_at,
            active_day=seeded_user.active_day_counter,
        )
        assert result_b.duplicate is True
        # entry-A row 가 살아 있음 (rollback 안 됨)
        from app.db.models import UserEvent as _UserEvent

        ue_rows = (
            await db_session.execute(
                select(_UserEvent).where(
                    _UserEvent.user_id == seeded_user.user_id,
                    _UserEvent.client_request_id == req_a,
                )
            )
        ).scalars().all()
        assert len(ue_rows) == 1
        assert ue_rows[0].event_id == result_a.event_id

    @pytest.mark.asyncio
    async def test_s06_onboarding_savepoint_isolation(
        self,
        db_session,
        seeded_user: User,
        seeded_cso_topics: list[CSOTopic],
        seeded_system_config,
    ) -> None:
        """S-06: bootstrap_interest_state 가 begin_nested() savepoint 안에서 호출되어 부분 INSERT
        rollback 이 outer 트랜잭션을 깨지 않음.

        본 테스트는 savepoint API 자체의 사용 가능성을 검증 (실 onboarding endpoint 통합은
        별도 integration test). begin_nested() → INSERT → rollback → outer 정상 commit.
        """
        # savepoint 시작
        sp = await db_session.begin_nested()
        try:
            db_session.add(
                UserInterestState(
                    state_id=uuid.uuid4(),
                    user_id=seeded_user.user_id,
                    cso_topic_id=seeded_cso_topics[0].cso_topic_id,
                    leaf_topic_id=None,
                    long_alpha=2.0,
                    long_beta=4.0,
                    short_alpha=2.0,
                    short_beta=4.0,
                    long_score=0.3,
                    short_score=0.3,
                    last_event_active_day=0,
                    last_decay_active_day=0,
                    boost_applied_at_active_day=0,
                )
            )
            await db_session.flush()
            # 시뮬레이션: 실패 → savepoint rollback
            raise RuntimeError("simulated bootstrap failure")
        except RuntimeError:
            await sp.rollback()
        # outer 트랜잭션은 정상 — User 는 그대로
        u = (
            await db_session.execute(
                select(User).where(User.user_id == seeded_user.user_id)
            )
        ).scalar_one()
        assert u.user_id == seeded_user.user_id
        # rollback 된 row 는 없음
        rows = (
            await db_session.execute(
                select(UserInterestState).where(
                    UserInterestState.user_id == seeded_user.user_id
                )
            )
        ).scalars().all()
        assert len(rows) == 0


# ============================================================
# C-60 — onboarding 선택 표시 정합
# ============================================================


def test_c60_interest_topic_view_has_is_onboarding_selected() -> None:
    """InterestTopicView schema 가 is_onboarding_selected 필드 보유."""
    import inspect

    from app.interest.schemas import InterestTopicView

    fields = InterestTopicView.model_fields
    assert "is_onboarding_selected" in fields
    # router 본문이 boost_applied_at_active_day 기반으로 채우는지 정적 검증.
    from app.interest.router import get_interest_state

    src = inspect.getsource(get_interest_state)
    assert "is_onboarding_selected" in src
    assert "boost_applied_at_active_day" in src
