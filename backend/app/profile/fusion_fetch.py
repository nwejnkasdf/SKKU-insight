"""C-54 (2026-05-24) Fusion bridge_cso 영역 fresh Document fetch.

UserProfile cron 안에서 BFS bridge_cso 결정 직후 호출 — LLM web_search 도구로
두 trace 의 외부 교차 영역에 대한 fresh 자료 fetch + Document/DocumentTopic INSERT.

사용자 결정 매트릭스 (decisions.md §17):
- A1: cron 안 (apply_fusion_bridge_override 끝에)
- B2: bridge_cso + 두 path 라벨 + 두 trace 최근 saved Document 제목 각 3개
- C1: 기존 collection_job 의 LLM schema 재사용 (provider.search_with_tools)
- D : bridge 매핑은 bridge_cso 단일 (LeafTarget(parent=bridge_cso, leaf=None))
- E1: 매일 fresh fetch (조건 가드 없음 — narrative 차원)
- F1: 실패 시 fusion_candidates 보존 + INSERT 안 함 → dashboard 빈 풀 fallback trend

P1 dedup hint: 직전 N일 (FUSION_FETCH_RECENT_URLS_WINDOW_DAYS=30) 의
Recommendation 의 origin_type='fusion' 카드 → Document.canonical_url + title list
를 trace_json["seen_urls"]/"seen_titles" 에 박음. 기존 collection prompt §2 dedup
hint 가 자연 적용.

Anti-pattern 회피:
- A4/A6/A7 lesson: on_conflict_do_nothing + greatest(confidence) UPSERT 패턴
  (orchestrator._insert_document_idempotent / _upsert_document_topic 재사용)
- C-53 lesson: bridge_cso 가 이미 apply_fusion_bridge_override 에서 cso_graph
  멤버십 검증된 노드 — fusion_fetch 추가 가드 불필요
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.orchestrator import (
    LeafTarget,
    _get_llm_search_source_id,
    _insert_document_idempotent,
    _upsert_document_topic,
)
from app.db.models import (
    Document,
    DocumentTopic,
    Recommendation,
    SavedDocument,
    UserCSOTraversal,
)
from app.llm_provider.protocol import LLMProvider, ProviderError, SearchResult

logger = logging.getLogger(__name__)

_RECENT_SAVED_TITLES_PER_TRACE = 3


async def fetch_trace_saved_titles(
    db: AsyncSession,
    user_id: UUID,
    trace_path: list[UUID],
    *,
    limit: int = _RECENT_SAVED_TITLES_PER_TRACE,
) -> list[str]:
    """trace path 의 cso_topic 들과 매핑된 SavedDocument 의 Document.title list.

    P1 prompt context B2 — 두 trace 각각의 최근 saved Document 제목 limit 개. SavedDocument
    saved_at MAX DESC 정렬. trace.path 위 어떤 cso 든 매핑된 자료면 포함 (path 전체 =
    해당 trace 의 관심 영역). 같은 title 여러 row 면 가장 최근 save 시각으로 통합.

    (2026-05-27 fix) SELECT DISTINCT + ORDER BY non-select column 은 PostgreSQL 에서
    InvalidColumnReferenceError. GROUP BY title + MAX(saved_at) 패턴으로 교체 —
    semantic 동일 (distinct titles ordered by recency) + SQL 표준 준수.
    """
    if not trace_path:
        return []
    stmt = (
        select(Document.title, func.max(SavedDocument.saved_at).label("last_save"))
        .join(
            DocumentTopic,
            DocumentTopic.document_id == Document.document_id,
        )
        .join(
            SavedDocument,
            SavedDocument.document_id == Document.document_id,
        )
        .where(
            SavedDocument.user_id == user_id,
            DocumentTopic.cso_topic_id.in_(trace_path),
        )
        .group_by(Document.title)
        .order_by(func.max(SavedDocument.saved_at).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [str(row.title) for row in rows if row.title]


async def _fetch_recent_fusion_titles_urls(
    db: AsyncSession,
    user_id: UUID,
    *,
    window_days: int,
) -> tuple[list[str], list[str]]:
    """직전 N일 fusion 카드의 Document.canonical_url/title list — P1 dedup hint.

    Recommendation 의 origin_type='fusion' + 본 window 안 row 조회. Document JOIN.
    canonical_url NULL 이면 url fallback (어쨌든 LLM 회피용 텍스트).
    """
    if window_days <= 0:
        return [], []
    window_start = datetime.now(UTC) - timedelta(days=window_days)
    stmt = (
        select(Document.canonical_url, Document.url, Document.title)
        .join(Recommendation, Recommendation.document_id == Document.document_id)
        .where(
            Recommendation.user_id == user_id,
            Recommendation.origin_type == "fusion",
            Recommendation.created_at >= window_start,
        )
        .distinct()
    )
    rows = (await db.execute(stmt)).all()
    urls: list[str] = []
    titles: list[str] = []
    for row in rows:
        url = row.canonical_url or row.url
        if url:
            urls.append(url)
        if row.title:
            titles.append(row.title)
    return urls, titles


def _build_trace_json(
    *,
    bridge_label: str,
    bridge_cso_topic_id: UUID,
    archived_path_labels: list[str],
    active_path_labels: list[str],
    archived_saved_titles: list[str],
    active_saved_titles: list[str],
    seen_urls: list[str],
    seen_titles: list[str],
) -> dict[str, Any]:
    """fusion 맥락을 search_with_tools 의 trace_json 인자에 박는 dict 빌더.

    기존 collection_job prompt 가 "leaf_label 가 가장 강한 검색 신호" + "trace_json
    의 user trace + 선택 cluster" 라고 명시. fusion 의 경우 leaf_label 자리에
    bridge_label, trace_json 자리에 두 trace 의 fusion context 를 넣으면 자연 적용.

    prompt §2 dedup hint 의 "seen_urls" / "seen_titles" 키를 그대로 사용 — 동일 prompt.
    """
    return {
        "task": "fusion_bridge_fetch",
        "task_description_ko": (
            "사용자의 옛 관심 영역(archived) 과 현재 관심 영역(active) 가 만나는 "
            "'fusion bridge' 토픽 영역의 fresh 자료를 web 검색 도구로 수집한다. "
            "두 trace 의 외부 교차 — meet in the middle BFS 의 첫 만남 노드. "
            "narrative: 두 영역 사이의 새 학습 path (예: Graph Algorithms x Memory "
            "Management = Memory-bounded Algorithms)."
        ),
        "bridge": {
            "label": bridge_label,
            "cso_topic_id": str(bridge_cso_topic_id),
        },
        "archived_trace": {
            "path_labels": archived_path_labels,
            "recent_saved_titles": archived_saved_titles,
        },
        "active_trace": {
            "path_labels": active_path_labels,
            "recent_saved_titles": active_saved_titles,
        },
        "seen_urls": seen_urls,
        "seen_titles": seen_titles,
    }


async def fetch_fusion_documents(
    db: AsyncSession,
    provider: LLMProvider,
    *,
    user_id: UUID,
    bridge_cso_topic_id: UUID,
    bridge_label: str,
    archived_trace: UserCSOTraversal,
    active_trace: UserCSOTraversal,
    archived_path_labels: list[str],
    active_path_labels: list[str],
    archived_saved_titles: list[str],
    active_saved_titles: list[str],
    max_documents: int,
    recent_urls_window_days: int,
) -> int:
    """LLM web_search 호출 + Document/DocumentTopic INSERT.

    Args:
        bridge_cso_topic_id: BFS 가 결정한 fusion bridge 노드 (cso_graph 멤버 검증된 ID)
        bridge_label: bridge 라벨 (leaf_label 자리에 사용)
        archived_trace / active_trace: BFS 입력으로 사용된 두 trace (path 보존)
        archived_path_labels / active_path_labels: path 의 사람 친화 라벨 list
        archived_saved_titles / active_saved_titles: 각 trace 의 최근 saved Document 제목 (B2)
        max_documents: LLM 결과 cap (collection_job 과 동일 5)
        recent_urls_window_days: 직전 N일 fusion fetch URL/title 회피 window

    Returns:
        신규 INSERT 된 Document 수 (통계용).

    실패 모드 (F1): ProviderError 는 caller (apply_fusion_bridge_override) 가 catch.
    본 함수가 ProviderError 를 그대로 raise 하면 caller 가 logger.warning + 정상 흐름
    유지 (fusion_candidates 보존, Document INSERT 0건).
    """
    sentinel_source_id = await _get_llm_search_source_id(db)
    seen_urls, seen_titles = await _fetch_recent_fusion_titles_urls(
        db, user_id, window_days=recent_urls_window_days
    )
    trace_json = _build_trace_json(
        bridge_label=bridge_label,
        bridge_cso_topic_id=bridge_cso_topic_id,
        archived_path_labels=archived_path_labels,
        active_path_labels=active_path_labels,
        archived_saved_titles=archived_saved_titles,
        active_saved_titles=active_saved_titles,
        seen_urls=seen_urls,
        seen_titles=seen_titles,
    )
    try:
        results: list[SearchResult] = await provider.search_with_tools(
            trace_json,
            bridge_label,
            top_n=max_documents,
            user_id=str(user_id),
        )
    except ProviderError as exc:
        logger.warning(
            "fusion_fetch provider error user=%s bridge=%s err=%s",
            user_id,
            bridge_cso_topic_id,
            exc,
        )
        return 0

    if not results:
        logger.info(
            "fusion_fetch empty results user=%s bridge=%s", user_id, bridge_cso_topic_id
        )
        return 0

    # D: bridge_cso 단일 매핑. LeafTarget(parent=bridge_cso, leaf=None) 으로
    # _upsert_document_topic 의 partial UNIQUE 분기 (leaf NULL + cso NOT NULL) 사용.
    bridge_leaf = LeafTarget(
        leaf_label=bridge_label,
        parent_cso_topic_id=bridge_cso_topic_id,
        leaf_topic_id=None,
    )
    inserted_count = 0
    for r in results:
        doc_id, is_new = await _insert_document_idempotent(
            db, sentinel_source_id, r
        )
        if doc_id is None:
            continue
        await _upsert_document_topic(db, doc_id, bridge_leaf, r.confidence)
        if is_new:
            inserted_count += 1
    logger.info(
        "fusion_fetch user=%s bridge=%s fetched=%d inserted=%d",
        user_id,
        bridge_cso_topic_id,
        len(results),
        inserted_count,
    )
    return inserted_count


__all__ = ["fetch_fusion_documents", "fetch_trace_saved_titles"]
