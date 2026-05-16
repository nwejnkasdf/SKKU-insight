"""topic_distribution.pick_max_confidence — not-interested 하이브리드의 최고 confidence 선택.

deterministic tiebreak:
1) confidence DESC
2) cso_topic_id 우선 (cso != None > leaf only)
3) UUID 오름차순
"""
from __future__ import annotations

from uuid import UUID

from app.interest.topic_distribution import (
    DocumentTopicMapping,
    pick_max_confidence,
)


def _m(
    cso: str | None = None, leaf: str | None = None, conf: float = 0.5
) -> DocumentTopicMapping:
    return DocumentTopicMapping(
        document_topic_id=UUID("00000000-0000-0000-0000-000000000001"),
        cso_topic_id=UUID(cso) if cso else None,
        leaf_topic_id=UUID(leaf) if leaf else None,
        confidence=conf,
    )


class TestPickMaxConfidence:
    def test_empty_returns_none(self) -> None:
        assert pick_max_confidence([]) is None

    def test_picks_highest_confidence(self) -> None:
        m1 = _m(cso="11111111-1111-1111-1111-111111111111", conf=0.3)
        m2 = _m(cso="22222222-2222-2222-2222-222222222222", conf=0.8)
        m3 = _m(cso="33333333-3333-3333-3333-333333333333", conf=0.5)
        picked = pick_max_confidence([m1, m2, m3])
        assert picked is m2

    def test_tiebreak_cso_over_leaf_only(self) -> None:
        # 동일 confidence — cso != None 우선
        m_leaf = _m(leaf="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", conf=0.5)
        m_cso = _m(cso="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", conf=0.5)
        picked = pick_max_confidence([m_leaf, m_cso])
        assert picked is m_cso

    def test_tiebreak_uuid_asc(self) -> None:
        # 동일 confidence + 둘 다 cso → UUID 오름차순
        m_b = _m(cso="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", conf=0.5)
        m_a = _m(cso="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", conf=0.5)
        picked = pick_max_confidence([m_b, m_a])
        assert picked is m_a
