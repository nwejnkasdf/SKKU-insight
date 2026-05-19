"""ORM 모델 단일 진입점. alembic raw DDL 의 1:1 미러링.

연관 docs:
- docs/data/schema.md      — 본 모델들의 명세 (SOR)
- docs/sdd/contracts.md    — enum SOR (status·role 등)
- docs/sdd/module-boundaries.md `app/db`

마이그레이션 ↔ 모델 매핑:
- 0001: A2 — User, AdminUser, UserConsent, UserCSOTraversal, BroadInterest, CSOTopic, Source, SourcePolicy
- 0002: A3 — CSOTopicParent, DynamicLeafTopic, DynamicLeafTopicCSOTopic
- 0003: A4 — Document, DocumentTopic, CollectionJob, ClickbaitResult
- 0004: A6 — UserEvent, UserInterestState, SavedDocument, HiddenDocument, NotInterestedTopic, SystemConfig
- 0005: A7 — UserCSOTraversal.merged_into_trace_id 컬럼 (테이블 자체는 0001)
- 0006: A8 — Recommendation, RecommendationSlot, DocumentSummaryCache
- 0007: A9 — UserProfile (1:1 user, daily LLM cron 생성, discovery slot input SOR)
"""
from __future__ import annotations

from app.db.models.admin_user import AdminUser
from app.db.models.broad_interest import BroadInterest
from app.db.models.clickbait_result import ClickbaitResult
from app.db.models.collection_job import CollectionJob
from app.db.models.cso_topic import CSOTopic
from app.db.models.cso_topic_parent import CSOTopicParent
from app.db.models.document import Document
from app.db.models.document_summary_cache import DocumentSummaryCache
from app.db.models.document_topic import DocumentTopic
from app.db.models.dynamic_leaf_topic import DynamicLeafTopic
from app.db.models.dynamic_leaf_topic_cso_topic import DynamicLeafTopicCSOTopic
from app.db.models.hidden_document import HiddenDocument
from app.db.models.not_interested_topic import NotInterestedTopic
from app.db.models.recommendation import Recommendation
from app.db.models.recommendation_slot import RecommendationSlot
from app.db.models.saved_document import SavedDocument
from app.db.models.source import Source
from app.db.models.source_policy import SourcePolicy
from app.db.models.system_config import SystemConfig
from app.db.models.user import User
from app.db.models.user_consent import UserConsent
from app.db.models.user_cso_traversal import UserCSOTraversal
from app.db.models.user_event import UserEvent
from app.db.models.user_interest_state import UserInterestState
from app.db.models.user_profile import UserProfile

__all__ = [
    "AdminUser",
    "BroadInterest",
    "CSOTopic",
    "CSOTopicParent",
    "ClickbaitResult",
    "CollectionJob",
    "Document",
    "DocumentSummaryCache",
    "DocumentTopic",
    "DynamicLeafTopic",
    "DynamicLeafTopicCSOTopic",
    "HiddenDocument",
    "NotInterestedTopic",
    "Recommendation",
    "RecommendationSlot",
    "SavedDocument",
    "Source",
    "SourcePolicy",
    "SystemConfig",
    "User",
    "UserCSOTraversal",
    "UserConsent",
    "UserEvent",
    "UserInterestState",
    "UserProfile",
]
