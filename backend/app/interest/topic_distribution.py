"""Event → 토픽 분배. decision-backlog P1-4 default + interest-bayesian.md §토픽 분배.

case 1: event.cso_topic_id / leaf_topic_id 직접 지정 → 100% 단일 토픽.
case 2: document_id 만 → DocumentTopic 모든 row confidence 정규화 분배 (P1-4).
case 3: 토픽도 문서도 없음 → 빈 list (event INSERT 만, posterior skip).

not_interested 문서 단위 요청은 service.py 에서 posterior 갱신을 우회한다.
토픽 단위 "분야 줄이기" 요청만 case 1 경로로 점수에 반영한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentTopic


@dataclass(frozen=True)
class TopicAssignment:
    """베이지안 UPSERT 대상 (user, topic) 쌍 + 분배 비율."""

    cso_topic_id: UUID | None
    leaf_topic_id: UUID | None
    weight: float  # 0.0 ~ 1.0 (합 = 1)


@dataclass(frozen=True)
class DocumentTopicMapping:
    """DocumentTopic row 의 dataclass view — service 가 최고 confidence 추출에도 사용."""

    document_topic_id: UUID
    cso_topic_id: UUID | None
    leaf_topic_id: UUID | None
    confidence: float


async def lookup_document_topics(
    db: AsyncSession, document_id: UUID
) -> list[DocumentTopicMapping]:
    """Document 의 DocumentTopic row 모두 (cso/leaf/confidence)."""
    rows = (
        await db.execute(
            select(
                DocumentTopic.id,
                DocumentTopic.cso_topic_id,
                DocumentTopic.leaf_topic_id,
                DocumentTopic.confidence,
            ).where(DocumentTopic.document_id == document_id)
        )
    ).all()
    return [
        DocumentTopicMapping(
            document_topic_id=row.id,
            cso_topic_id=row.cso_topic_id,
            leaf_topic_id=row.leaf_topic_id,
            confidence=float(row.confidence),
        )
        for row in rows
    ]


async def resolve_topic_distribution(
    db: AsyncSession,
    *,
    document_id: UUID | None,
    cso_topic_id: UUID | None,
    leaf_topic_id: UUID | None,
) -> list[TopicAssignment]:
    """event → (topic, weight) 리스트. weight 합 = 1.0 (분배가 있으면).

    명시 지정 토픽이 있으면 그 토픽에 100%. 없으면 DocumentTopic 정규화 분배.
    """
    # case 1: 토픽 직접 지정 — 100%
    if cso_topic_id is not None or leaf_topic_id is not None:
        return [
            TopicAssignment(
                cso_topic_id=cso_topic_id,
                leaf_topic_id=leaf_topic_id,
                weight=1.0,
            )
        ]
    # case 3: 문서·토픽 둘 다 없음
    if document_id is None:
        return []
    # case 2: DocumentTopic 정규화 분배 (P1-4 default)
    mappings = await lookup_document_topics(db, document_id)
    if not mappings:
        return []
    total = sum(m.confidence for m in mappings)
    if total <= 0:
        return []
    return [
        TopicAssignment(
            cso_topic_id=m.cso_topic_id,
            leaf_topic_id=m.leaf_topic_id,
            weight=m.confidence / total,
        )
        for m in mappings
    ]


def pick_max_confidence(
    mappings: list[DocumentTopicMapping],
) -> DocumentTopicMapping | None:
    """not-interested 하이브리드 — 최고 confidence row 1건.

    동률 시 deterministic: cso_topic_id IS NOT NULL 우선 (토픽 거부 의도 명확), 그 다음
    UUID 오름차순.
    """
    if not mappings:
        return None

    def _key(m: DocumentTopicMapping) -> tuple[float, int, str]:
        # 낮을수록 우선 — confidence DESC 위해 음수, cso 우선 위해 0, 그 다음 UUID asc
        cso_priority = 0 if m.cso_topic_id is not None else 1
        topic_id_str = str(m.cso_topic_id or m.leaf_topic_id or "")
        return (-m.confidence, cso_priority, topic_id_str)

    return sorted(mappings, key=_key)[0]


__all__ = [
    "DocumentTopicMapping",
    "TopicAssignment",
    "lookup_document_topics",
    "pick_max_confidence",
    "resolve_topic_distribution",
]
