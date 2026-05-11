"""A2 8 ORM 모델 + A3 가 추가할 3 모델의 단일 진입점.

A2 PR #7 이 alembic raw DDL 만 작성하고 SQLAlchemy ORM 모델 파일은 누락한 drift
를 본 hotfix PR 이 메운다. 모든 모델은 `backend/alembic/versions/0001_initial_a2_tables.py`
의 컬럼·CHECK·index 를 1:1 미러링 → `alembic check` autogenerate diff = 0 보장.

연관 docs:
- docs/data/schema.md      — 본 모델들의 명세 (SOR)
- docs/sdd/contracts.md    — enum SOR (status·role 등)
- docs/sdd/module-boundaries.md `app/db`
"""
from __future__ import annotations

from app.db.models.admin_user import AdminUser
from app.db.models.broad_interest import BroadInterest
from app.db.models.cso_topic import CSOTopic
from app.db.models.source import Source
from app.db.models.source_policy import SourcePolicy
from app.db.models.user import User
from app.db.models.user_consent import UserConsent
from app.db.models.user_cso_traversal import UserCSOTraversal

__all__ = [
    "AdminUser",
    "BroadInterest",
    "CSOTopic",
    "Source",
    "SourcePolicy",
    "User",
    "UserCSOTraversal",
    "UserConsent",
]
