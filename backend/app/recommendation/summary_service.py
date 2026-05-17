"""GET /documents/{id}/summary — DocumentSummaryCache 로딩 + LLM medium 생성.

흐름 (recommendation-ranking.md + cold-start 의 pseudo-Document 참고):
1. DB lookup DocumentSummaryCache(PK=document_id) → hit 시 즉시 응답.
2. miss → Document fetch (404 if not exist).
3. acquire_slot(None) + asyncio.timeout + LLM complete(medium, json).
4. parse 4 sections + reason_short.
5. INSERT on_conflict_do_nothing(index_elements=["document_id"]) — 동시 2 요청 race 차단.
6. db.commit() → 응답 (generator="llm"). §11.#1 cache-before-commit 회피 — DB 가 1차 SOR.
7. 예외 (ProviderError / Timeout / ValidationError) → Document.summary 있으면 generator
   ="source_abstract" + 1 section 응답. 없으면 503 `document.summary_unavailable`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.contracts import ErrorCode
from app.db.models import Document, DocumentSummaryCache
from app.llm_provider._concurrency import acquire_slot
from app.llm_provider.protocol import (
    ChatMessage,
    LLMBudgetExceeded,
    LLMProvider,
    ProviderError,
)

from .schemas import DocumentSummaryResponse, DocumentSummarySection

logger = logging.getLogger(__name__)


_VALID_SECTIONS = ("core", "background", "significance", "limitations")
_REASON_MAX_LENGTH = 80


def _build_summary_prompt(doc: Document) -> list[ChatMessage]:
    """system: 4 섹션 (core/background/significance/limitations) 한국어 요약 + reason."""
    system = (
        "당신은 사용자에게 기술 문서를 한국어로 요약하는 어시스턴트다. 정확히 4개 섹션 "
        "(core, background, significance, limitations) 으로 작성한다. 각 섹션 본문은 "
        "1-2 문단 (한국어). 점수·알고리즘·랭킹·확률 같은 시스템 용어는 언급하지 않는다. "
        "외부 원문을 그대로 복사하지 말고 본인 말로 요약한다 (NFR-25)."
    )
    user = (
        "[문서 정보]\n"
        f"title: {doc.title}\n"
        f"abstract: {doc.summary or '(없음)'}\n"
        f"published_at: {doc.published_at.isoformat() if doc.published_at else 'null'}\n"
        f"content_type: {doc.content_type}\n\n"
        "[지시]\n"
        "응답 형식: {\"sections\": [\n"
        "  {\"section\": \"core\", \"title_ko\": \"...\", \"body_ko\": \"...\"},\n"
        "  {\"section\": \"background\", \"title_ko\": \"...\", \"body_ko\": \"...\"},\n"
        "  {\"section\": \"significance\", \"title_ko\": \"...\", \"body_ko\": \"...\"},\n"
        "  {\"section\": \"limitations\", \"title_ko\": \"...\", \"body_ko\": \"...\"}\n"
        "], \"reason_short_ko\": \"60자 이내 한국어 추천 이유 1문장\"}\n"
        "JSON 으로만 응답한다."
    )
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]


def _parse_summary_response(
    parsed: object,
) -> tuple[list[DocumentSummarySection], str]:
    """LLM JSON 응답 → 4 섹션 + reason_short. 검증 실패 시 ValueError."""
    if not isinstance(parsed, dict):
        raise ValueError("response not object")
    raw_sections = parsed.get("sections")
    if not isinstance(raw_sections, list) or len(raw_sections) != 4:
        raise ValueError(f"sections must be list of 4, got {raw_sections!r}")
    sections: list[DocumentSummarySection] = []
    seen_sections: set[str] = set()
    for raw in raw_sections:
        if not isinstance(raw, dict):
            raise ValueError("section item not object")
        section_name = raw.get("section")
        if section_name not in _VALID_SECTIONS or section_name in seen_sections:
            raise ValueError(f"invalid or duplicate section: {section_name!r}")
        seen_sections.add(section_name)
        title_ko = raw.get("title_ko", "")
        body_ko = raw.get("body_ko", "")
        if not isinstance(title_ko, str) or not isinstance(body_ko, str):
            raise ValueError("title_ko/body_ko not string")
        sections.append(
            DocumentSummarySection(
                section=section_name,
                title_ko=title_ko.strip(),
                body_ko=body_ko.strip(),
            )
        )
    if len(seen_sections) != 4:
        raise ValueError(f"sections missing some of {_VALID_SECTIONS}")
    reason_short_raw = parsed.get("reason_short_ko", "")
    if not isinstance(reason_short_raw, str):
        reason_short_raw = ""
    reason_short = reason_short_raw.strip()
    if len(reason_short) > _REASON_MAX_LENGTH:
        reason_short = reason_short[: _REASON_MAX_LENGTH - 1] + "…"
    # 섹션 순서 정합 — 항상 _VALID_SECTIONS 순서로 정렬.
    sections.sort(key=lambda s: _VALID_SECTIONS.index(s.section))
    return sections, reason_short


def _source_abstract_fallback(
    doc: Document, settings: Settings
) -> DocumentSummaryResponse:
    """LLM 실패 시 Document.summary[:N] 1 section fallback. summary 없으면 503."""
    if not doc.summary:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": ErrorCode.DOCUMENT_SUMMARY_UNAVAILABLE.value,
                "message": "요약 서비스 일시 불가",
            },
        )
    body = doc.summary[: settings.DOCUMENT_SUMMARY_SOURCE_ABSTRACT_MAX_CHARS]
    section = DocumentSummarySection(
        section="core", title_ko="요약", body_ko=body
    )
    return DocumentSummaryResponse(
        document_id=doc.document_id,
        sections=[section],
        generator="source_abstract",
        generated_at=datetime.now(UTC),
        reason_short="자동 요약 일시 불가",
    )


async def get_or_build_summary(
    db: AsyncSession,
    redis: aioredis.Redis,
    provider: LLMProvider,
    settings: Settings,
    document_id: UUID,
) -> DocumentSummaryResponse:
    """endpoint 진입점. cache hit → 즉시 / miss → LLM + INSERT / 실패 → fallback."""
    # 1. cache lookup (DocumentSummaryCache PK).
    cached = await db.get(DocumentSummaryCache, document_id)
    if cached is not None:
        sections = [
            DocumentSummarySection(
                section=item.get("section", "core"),
                title_ko=item.get("title_ko", ""),
                body_ko=item.get("body_ko", ""),
            )
            for item in cached.sections
            if isinstance(item, dict)
            and item.get("section") in _VALID_SECTIONS
        ]
        return DocumentSummaryResponse(
            document_id=document_id,
            sections=sections,
            generator=cached.generator,
            generated_at=cached.generated_at,
            reason_short=cached.reason_short,
        )

    # 2. Document fetch.
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": ErrorCode.DOCUMENT_NOT_FOUND.value,
                "message": "문서를 찾을 수 없습니다.",
            },
        )

    # 3. LLM 호출 (timeout + semaphore).
    try:
        async with asyncio.timeout(settings.DOCUMENT_SUMMARY_LLM_TIMEOUT_SECONDS):
            async with acquire_slot(None):
                resp = await provider.complete(
                    _build_summary_prompt(doc),
                    model_slot="medium",
                    response_format="json",
                )
        sections, reason_short = _parse_summary_response(resp.parsed_json)
        # 4. INSERT (race-safe on_conflict).
        stmt = (
            pg_insert(DocumentSummaryCache)
            .values(
                document_id=document_id,
                sections=[s.model_dump() for s in sections],
                reason_short=reason_short,
                model_used=resp.model,
                generator="llm",
            )
            .on_conflict_do_nothing(index_elements=["document_id"])
        )
        await db.execute(stmt)
        # 5. commit 성공 후 응답.
        await db.commit()
        # 동시 race 시 INSERT skip 된 경우 DB 의 다른 row 가 SOR 이지만 응답은 자기 결과 유지.
        return DocumentSummaryResponse(
            document_id=document_id,
            sections=sections,
            generator="llm",
            generated_at=datetime.now(UTC),
            reason_short=reason_short,
        )
    except (TimeoutError, ProviderError, LLMBudgetExceeded, ValueError, json.JSONDecodeError) as exc:
        await db.rollback()
        logger.warning(
            "summary LLM failed; fallback: doc=%s err=%s",
            document_id,
            exc,
        )
        return _source_abstract_fallback(doc, settings)


__all__ = ["get_or_build_summary"]
