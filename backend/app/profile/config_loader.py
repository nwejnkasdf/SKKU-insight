"""Settings → ProfileGeneratorConfig (immutable dataclass) 변환.

worker cron + service 가 Settings 객체를 직접 의존하지 않고 본 dataclass 만 받음 —
테스트 격리 + 변경 추적 용이.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True, slots=True)
class ProfileGeneratorConfig:
    """A8-v2 daily user_profile cron + service 가 사용하는 설정 묶음.

    `decisions.md §15` 결정 매트릭스 + `ops/env-vars.md` §A9.
    """

    cron_expr: str
    archive_score_tail_min: float
    generator_version: str
    input_archive_max: int
    reincarnation_gap_days_min: int
    lock_ttl_seconds: int
    cache_ttl_seconds: int


def load_profile_config(settings: Settings) -> ProfileGeneratorConfig:
    """Settings → ProfileGeneratorConfig. 즉시 호출 — 캐시 외부 (lru_cache settings 의존)."""
    return ProfileGeneratorConfig(
        cron_expr=settings.USER_PROFILE_CRON,
        archive_score_tail_min=settings.USER_PROFILE_ARCHIVE_SCORE_TAIL_MIN,
        generator_version=settings.USER_PROFILE_GENERATOR_VERSION,
        input_archive_max=settings.USER_PROFILE_INPUT_ARCHIVE_MAX,
        reincarnation_gap_days_min=settings.USER_PROFILE_REINCARNATION_GAP_DAYS_MIN,
        lock_ttl_seconds=settings.USER_PROFILE_LOCK_TTL_SECONDS,
        cache_ttl_seconds=settings.USER_PROFILE_CACHE_TTL_SECONDS,
    )


__all__ = ["ProfileGeneratorConfig", "load_profile_config"]
