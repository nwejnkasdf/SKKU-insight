"""A8-v2 UserProfile generation 모듈.

daily LLM cron (`app/worker/jobs/user_profile.py`) 가 본 패키지의 함수를 호출해
사용자별 캐릭터 프로파일 + fusion seeds 를 생성·영속한다. discovery slot 2 (Fusion 1 +
Reincarnation 1) 의 input SOR.

연관 docs:
- docs/decisions.md §15              — A8-v2 결정 매트릭스
- docs/decision-backlog.md C-42      — 라운드 lesson
- docs/algorithms/recommendation-ranking.md §Discovery — fusion / reincarnation 룰
- docs/data/schema.md UserProfile    — ORM 명세 (SOR)
- docs/sdd/contracts.md              — JobType / RedisKey / ErrorCode SOR
"""
from __future__ import annotations

from app.profile.config_loader import ProfileGeneratorConfig, load_profile_config
from app.profile.schemas import (
    USER_PROFILE_JSON_SCHEMA,
    FusionCandidate,
    ProfileLLMInput,
    TopicSeed,
    UserProfilePayload,
)

__all__ = [
    "USER_PROFILE_JSON_SCHEMA",
    "FusionCandidate",
    "ProfileGeneratorConfig",
    "ProfileLLMInput",
    "TopicSeed",
    "UserProfilePayload",
    "load_profile_config",
]
