"""system_config 로더 — interest_params + event_weights JSONB read-only.

lifespan startup 가 본 모듈을 호출해 Redis 캐시에 SETEX. 이후 service/decay 가 Redis
에서 read (TTL 60s). A10 admin-console 가 PUT /admin/system-config 시 Redis cache 명시 DEL
+ DB UPDATE — 다음 read 시 자동 refresh.

A6 는 read-only. write 는 A10 책임.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import ErrorCode, RedisKey
from app.db.models import SystemConfig

INTEREST_PARAMS_KEY = "interest_params"
EVENT_WEIGHTS_KEY = "event_weights"
SYSTEM_CONFIG_CACHE_TTL_SECONDS = 60


class SystemConfigMissingError(RuntimeError):
    """system_config seed row 가 비어 있음. lifespan startup 차단."""

    def __init__(self, key: str):
        self.key = key
        super().__init__(
            f"system_config row '{key}' 누락 — alembic 0004 seed 또는 A10 변경 확인 필요. "
            f"ErrorCode: {ErrorCode.INTEREST_SYSTEM_CONFIG_MISSING.value}"
        )


@dataclass(frozen=True)
class InterestParams:
    """interest-bayesian.md §구성 파일 스키마. system_config row JSONB 의 dataclass mirror."""

    alpha_prior: float
    beta_prior: float
    half_life_short_active_days: float
    half_life_long_active_days: float
    onboarding_prior_boost: float
    onboarding_boost_active_days: int
    propagation_hop_decay: float
    propagation_max_hops: int
    propagation_non_trace_ancestors: bool
    bucket_high_long: float
    bucket_high_short: float
    bucket_medium: float
    bucket_low: float

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> InterestParams:
        return cls(
            alpha_prior=float(raw["alpha_prior"]),
            beta_prior=float(raw["beta_prior"]),
            half_life_short_active_days=float(raw["half_life_short_active_days"]),
            half_life_long_active_days=float(raw["half_life_long_active_days"]),
            onboarding_prior_boost=float(raw["onboarding_prior_boost"]),
            onboarding_boost_active_days=int(raw["onboarding_boost_active_days"]),
            propagation_hop_decay=float(raw["propagation_hop_decay"]),
            propagation_max_hops=int(raw["propagation_max_hops"]),
            propagation_non_trace_ancestors=bool(
                raw["propagation_non_trace_ancestors"]
            ),
            bucket_high_long=float(raw["bucket_high_long"]),
            bucket_high_short=float(raw["bucket_high_short"]),
            bucket_medium=float(raw["bucket_medium"]),
            bucket_low=float(raw["bucket_low"]),
        )


@dataclass(frozen=True)
class EventWeights:
    """event_weights.toml 구조. weights dict + caps dict."""

    weights: dict[str, float]
    dwell_tick_max_per_document: int
    weight_per_event_max: float

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EventWeights:
        weights_raw = raw.get("weights", {})
        caps_raw = raw.get("caps", {})
        return cls(
            weights={k: float(v) for k, v in weights_raw.items()},
            dwell_tick_max_per_document=int(
                caps_raw.get("dwell_tick_max_per_document", 4)
            ),
            weight_per_event_max=float(
                caps_raw.get("weight_per_event_max", 5.0)
            ),
        )

    def lookup(self, event_type: str) -> float:
        return self.weights.get(event_type, 0.0)


async def load_system_config(
    db: AsyncSession, redis: aioredis.Redis
) -> tuple[InterestParams, EventWeights]:
    """DB system_config 의 (interest_params, event_weights) 2 row → Redis SETEX 60s.

    lifespan startup 가 1회 호출. row 누락 시 SystemConfigMissingError (lifespan 차단).
    """
    rows = (
        await db.execute(
            select(SystemConfig.key, SystemConfig.value).where(
                SystemConfig.key.in_([INTEREST_PARAMS_KEY, EVENT_WEIGHTS_KEY])
            )
        )
    ).all()
    by_key: dict[str, dict[str, Any]] = {row.key: row.value for row in rows}
    if INTEREST_PARAMS_KEY not in by_key:
        raise SystemConfigMissingError(INTEREST_PARAMS_KEY)
    if EVENT_WEIGHTS_KEY not in by_key:
        raise SystemConfigMissingError(EVENT_WEIGHTS_KEY)
    params = InterestParams.from_dict(by_key[INTEREST_PARAMS_KEY])
    weights = EventWeights.from_dict(by_key[EVENT_WEIGHTS_KEY])
    # Redis 캐싱 — read hot path 용.
    await redis.setex(
        RedisKey.system_config_cache(INTEREST_PARAMS_KEY),
        SYSTEM_CONFIG_CACHE_TTL_SECONDS,
        json.dumps(by_key[INTEREST_PARAMS_KEY]),
    )
    await redis.setex(
        RedisKey.system_config_cache(EVENT_WEIGHTS_KEY),
        SYSTEM_CONFIG_CACHE_TTL_SECONDS,
        json.dumps(by_key[EVENT_WEIGHTS_KEY]),
    )
    return params, weights


async def get_interest_params(
    redis: aioredis.Redis, db: AsyncSession
) -> InterestParams:
    """Redis 캐시 우선 → miss 시 DB lookup + refresh."""
    cached = await redis.get(RedisKey.system_config_cache(INTEREST_PARAMS_KEY))
    if cached is not None:
        raw = json.loads(cached if isinstance(cached, str) else cached.decode())
        return InterestParams.from_dict(raw)
    # miss → DB
    params, _weights = await load_system_config(db, redis)
    return params


async def get_event_weights(
    redis: aioredis.Redis, db: AsyncSession
) -> EventWeights:
    """Redis 캐시 우선 → miss 시 DB lookup + refresh."""
    cached = await redis.get(RedisKey.system_config_cache(EVENT_WEIGHTS_KEY))
    if cached is not None:
        raw = json.loads(cached if isinstance(cached, str) else cached.decode())
        return EventWeights.from_dict(raw)
    _params, weights = await load_system_config(db, redis)
    return weights


__all__ = [
    "EVENT_WEIGHTS_KEY",
    "INTEREST_PARAMS_KEY",
    "EventWeights",
    "InterestParams",
    "SystemConfigMissingError",
    "get_event_weights",
    "get_interest_params",
    "load_system_config",
]
