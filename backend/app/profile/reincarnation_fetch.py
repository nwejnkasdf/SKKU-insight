"""Reincarnation 영역 fresh Document fetch — fusion_fetch.py 대칭.

UserProfile cron 안에서 archived trace softmax sampling 직후 호출 — LLM web_search
도구로 archived 영역의 fresh 자료 fetch + Document/DocumentTopic INSERT.

목적: Reincarnation 슬롯이 기존 corpus 의 누적 docs 에만 의존하면 archive 부활
narrative 가 빈 슬롯으로 떨어지는 빈도가 높다. archived 영역은 collection_job 의 active
leaves 대상이 아니므로 신규 docs 유입이 없음. fusion_fetch 와 동일 패턴으로 매일
sampled archived trace 의 tail_cso 영역에 fresh fetch — 잠시 멀어진 분야의 최근 동향
("내가 옛날에 보던 분야에 새 게 있네") 을 surface.

매핑: archived trace path 끝 CSO 단일 매핑 (LeafTarget(parent=tail_cso, leaf=None)).
recommendation engine 의 query_discovery_reincarnation 가 본 매핑을 catch
(`DocumentTopic.cso_topic_id == path_tail_cso_topic_id`).

실패 모드: ProviderError 는 caller (apply_reincarnation_prefetch) 가 catch — silent
skip, INSERT 0건. 다음 일자 cron 재시도.

사용자 결정 매트릭스: decisions.md §17 의 fusion_fetch 패턴 그대로 적용
(A1 cron 안 / B trace saved + 직전 fetch 회피 / C1 collection schema /
D tail_cso 단일 매핑 / E1 매일 fresh / F1 실패 보존).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.orchestrator import (
    LeafTarget,
    _get_llm_search_source_id,
    _insert_document_idempotent,
    _upsert_document_topic,
)
from app.db.models import Document, Recommendation, UserCSOTraversal
from app.llm_provider.protocol import LLMProvider, ProviderError, SearchResult

logger = logging.getLogger(__name__)


async def _fetch_recent_reincarnation_titles_urls(
    db: AsyncSession,
    user_id: UUID,
    *,
    window_days: int,
) -> tuple[list[str], list[str]]:
    """직전 N일 reincarnation 카드의 Document.canonical_url/title — fusion 의 dedup hint 대칭.

    Recommendation 의 origin_type='reincarnation' + 본 window 안 row 조회. Document JOIN.
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
            Recommendation.origin_type == "reincarnation",
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
    tail_label: str,
    tail_cso_topic_id: UUID,
    archived_path_labels: list[str],
    archived_leaf_labels: list[str],
    archived_saved_titles: list[str],
    seen_urls: list[str],
    seen_titles: list[str],
) -> dict[str, Any]:
    """reincarnation 맥락을 search_with_tools 의 trace_json 인자에 박는 dict 빌더.

    narrative: 사용자가 과거에 관심 가졌으나 잠시 멀어진 영역의 *최신* 동향. tail_label 이
    가장 강한 검색 신호 (leaf_label 자리에 사용), trace_json 의 archived_area 가 dedup
    + 관심 맥락 보강.

    prompt §2 dedup hint 의 "seen_urls" / "seen_titles" 키를 그대로 사용 — 동일 prompt.
    """
    return {
        "task": "reincarnation_fetch",
        "task_description_ko": (
            "사용자가 과거에 깊게 연구하다가 archive 된 영역의 *최신* 자료를 web 검색 "
            "도구로 수집한다. taste reincarnation — 과거 강한 신호로 끝난 영역에 최근 "
            "새로운 발전이 있다면 부활 가능성. narrative: '잊고 있던 옛 관심사가 "
            "다시 살아 돌아오는 순간'."
        ),
        "archived_area": {
            "tail_label": tail_label,
            "cso_topic_id": str(tail_cso_topic_id),
            "path_labels": archived_path_labels,
            "leaf_labels": archived_leaf_labels,
            "recent_saved_titles": archived_saved_titles,
        },
        "seen_urls": seen_urls,
        "seen_titles": seen_titles,
    }


async def fetch_reincarnation_documents(
    db: AsyncSession,
    provider: LLMProvider,
    *,
    user_id: UUID,
    tail_cso_topic_id: UUID,
    tail_label: str,
    archived_trace: UserCSOTraversal,
    archived_path_labels: list[str],
    archived_leaf_labels: list[str],
    archived_saved_titles: list[str],
    max_documents: int,
    recent_urls_window_days: int,
) -> int:
    """LLM web_search 호출 + Document/DocumentTopic INSERT — fusion_fetch 의 대칭.

    Args:
        tail_cso_topic_id: archived trace path 끝 CSO. recommendation engine 의
            query_discovery_reincarnation 가 본 ID 로 docs 매칭.
        tail_label: tail CSO 라벨 (leaf_label 자리 사용 — LLM 검색 신호).
        archived_trace: sampled archived trace (메타 추적용).
        archived_path_labels: path 사람 친화 라벨 list.
        archived_leaf_labels: 산하 archived/merged leaf 라벨 — LLM 검색 신호 강화.
        archived_saved_titles: 해당 trace 영역의 옛 saved Document 제목 (B 맥락).
        max_documents: LLM 결과 cap (collection_job 과 동일 5).
        recent_urls_window_days: 직전 N일 reincarnation fetch URL/title 회피 window.

    Returns:
        신규 INSERT 된 Document 수 (통계용).

    실패 모드 (F1): ProviderError 는 caller (apply_reincarnation_prefetch) 가 catch.
    본 함수가 ProviderError 를 그대로 raise 하면 caller 가 logger.warning + 정상 흐름
    유지 (INSERT 0건).
    """
    sentinel_source_id = await _get_llm_search_source_id(db)
    seen_urls, seen_titles = await _fetch_recent_reincarnation_titles_urls(
        db, user_id, window_days=recent_urls_window_days
    )
    trace_json = _build_trace_json(
        tail_label=tail_label,
        tail_cso_topic_id=tail_cso_topic_id,
        archived_path_labels=archived_path_labels,
        archived_leaf_labels=archived_leaf_labels,
        archived_saved_titles=archived_saved_titles,
        seen_urls=seen_urls,
        seen_titles=seen_titles,
    )
    try:
        results: list[SearchResult] = await provider.search_with_tools(
            trace_json,
            tail_label,
            top_n=max_documents,
            user_id=str(user_id),
        )
    except ProviderError as exc:
        logger.warning(
            "reincarnation_fetch provider error user=%s tail=%s err=%s",
            user_id,
            tail_cso_topic_id,
            exc,
        )
        return 0

    if not results:
        logger.info(
            "reincarnation_fetch empty results user=%s tail=%s",
            user_id,
            tail_cso_topic_id,
        )
        return 0

    # D: tail_cso 단일 매핑 — fusion 의 bridge_cso 단일 매핑 대칭. _upsert_document_topic
    # 의 partial UNIQUE 분기 (leaf NULL + cso NOT NULL) 사용.
    tail_target = LeafTarget(
        leaf_label=tail_label,
        parent_cso_topic_id=tail_cso_topic_id,
        leaf_topic_id=None,
    )
    inserted_count = 0
    for r in results:
        doc_id, is_new = await _insert_document_idempotent(
            db, sentinel_source_id, r
        )
        if doc_id is None:
            continue
        await _upsert_document_topic(db, doc_id, tail_target, r.confidence)
        if is_new:
            inserted_count += 1
    logger.info(
        "reincarnation_fetch user=%s tail=%s fetched=%d inserted=%d",
        user_id,
        tail_cso_topic_id,
        len(results),
        inserted_count,
    )
    return inserted_count


__all__ = ["fetch_reincarnation_documents"]
