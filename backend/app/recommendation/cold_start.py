"""Cold-start orchestrator — cold-start.md §처리 흐름.

worker/jobs/cold_start.py:cold_start_job 이 본 모듈의 `run_cold_start()` 호출.

절차:
1. Redis HSET status=running.
2. cap 검증: COLD_START_MAX_PER_USER_LIFETIME (사용자) + COLD_START_MAX_PER_DAY (전역).
3. LLM complete(high, json) — acquire_slot(user_id) + asyncio.timeout.
4. validate_cold_start — 10개 + 5/3/2 분배 + url_hint 검증 + reason ≤80자.
5. pseudo Document INSERT — sentinel `cold_start_pseudo` source_id.
   - url_hint 매칭 기존 Document 발견 시 재사용 (A4 dedup 패턴).
6. Recommendation x 10 + RecommendationSlot x 3 rows INSERT.
7. db.commit() 성공 후 → Redis cache SET + HSET completed (§11.#1 cache-before-commit 회피).
8. 예외 → db.rollback() + HSET failed + onboarding_lock 명시 DEL.

NFR-04: Recommendation.score 컬럼은 cold-start 카드는 0.0 (랭킹 미수행)
        — 응답 schema RecommendationCard 에는 score field 부재.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collection.dedup import normalize_title
from app.config import Settings
from app.contracts import (
    ContentType,
    ErrorCode,
    RedisKey,
    SentinelSource,
    SlotType,
    UserClass,
)
from app.db.models import (
    BroadInterest,
    Document,
    DocumentTopic,
    Recommendation,
    RecommendationSlot,
    Source,
    User,
)
from app.llm_provider._concurrency import acquire_slot
from app.llm_provider.protocol import (
    ChatMessage,
    LLMBudgetExceeded,
    LLMProvider,
    ProviderError,
)

logger = logging.getLogger(__name__)


class ColdStartFailed(Exception):
    """cold-start 실패 — caller 가 Redis HSET failed + error_code 저장."""

    def __init__(self, error_code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class ColdStartCapExceeded(ColdStartFailed):
    """사용자 lifetime 또는 전역 일 cap 초과."""


class InvalidColdStartResponse(ColdStartFailed):
    """LLM 응답 형식·분배·검증 실패."""


@dataclass(frozen=True, slots=True)
class ColdStartItem:
    """LLM 응답 1 item — validate_cold_start 통과 후."""

    slot_type: SlotType
    title: str
    title_ko: str | None
    publisher_domain: str | None
    publisher_label: str | None
    source_type: str   # academic | vendor_blog | tech_news
    url_hint: str | None
    doi_hint: str | None
    published_year: int | None
    related_csos_en: list[str]
    reason_short_ko: str


_SOURCE_TYPE_VALID = ("academic", "vendor_blog", "tech_news")
_SLOT_DISTRIBUTION = {SlotType.CORE: 5, SlotType.ADJACENT: 3, SlotType.DISCOVERY: 2}
_REASON_MAX_LENGTH = 80

_ACQUIRE_ONBOARDING_LOCK_LUA = """
local current = redis.call('GET', KEYS[1])
local current_request = redis.call('GET', KEYS[2])
if (not current) or current == ARGV[1] or
   (current == '1' and ((not current_request) or current_request == ARGV[1])) then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
    return 1
end
return 0
"""

_RELEASE_ONBOARDING_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
    if redis.call('GET', KEYS[2]) == ARGV[1] then
        redis.call('DEL', KEYS[2])
    end
    return 1
end
return 0
"""


def _build_cold_start_prompt(
    cluster_labels: list[str],
    user_class: UserClass,
    locale: str,
) -> list[ChatMessage]:
    """cold-start.md §LLM 프롬프트 그대로."""
    system = (
        "당신은 CS/AI 기술 동향 큐레이션 어시스턴트다. 사용자가 선택한 CSO 12 클러스터 "
        "중 1개 이상과 사용자 프로파일 메타를 보고, 그 사용자가 첫 대시보드에서 보면 "
        "가장 도움이 될 CS/AI 분야의 대표적이고 신뢰도 높은 최근 기술 동향 10개를 "
        "추천하라. 결과는 JSON 으로만 응답한다. 1차 출처(학술 논문, 빅테크 공식 블로그, "
        "공식 컨퍼런스)를 우선시하고 가짜 정보를 만들지 말 것 — 실제로 존재하는 자료만 "
        "추천한다."
    )
    user_content = (
        "[사용자 프로필]\n"
        f"- 사용자 클래스: {user_class.value}\n"
        f"- 가입 시각: {datetime.now(UTC).isoformat()}\n"
        f"- 선택한 CSO 클러스터: {json.dumps(cluster_labels, ensure_ascii=False)}\n"
        f"- 로케일: {locale}\n\n"
        "[지시]\n"
        "- 정확히 10개 후보.\n"
        "- 슬롯 분배: core 5 (선택 클러스터 직접 관련), adjacent 3 (인접 분야), "
        "discovery 2 (잠재적 흥미).\n"
        "- 각 후보 형식:\n"
        "  {\n"
        "    \"slot_type\": \"core\" | \"adjacent\" | \"discovery\",\n"
        "    \"title\": \"기사/논문 제목 (영문 원문 우선)\",\n"
        "    \"title_ko\": \"한국어 의역 1줄\",\n"
        "    \"publisher_domain\": \"arxiv.org | openai.com | deepmind.com | ...\",\n"
        "    \"publisher_label\": \"arXiv | OpenAI Blog | DeepMind | ...\",\n"
        "    \"source_type\": \"academic\" | \"vendor_blog\" | \"tech_news\",\n"
        "    \"url_hint\": \"정확한 URL 모르면 null. 추측 금지.\",\n"
        "    \"doi_hint\": \"DOI 알면 명시. 모르면 null.\",\n"
        "    \"published_year\": 2024 | 2025 | 2026 | null,\n"
        "    \"related_csos_en\": [\"Computer Vision\", \"Reinforcement Learning\"],\n"
        "    \"reason_short_ko\": \"한국어 한 문장 추천 이유 (60자 이내). "
        "점수나 알고리즘 언급 금지.\"\n"
        "  }\n"
        "- 가짜 URL/DOI/제목을 만들지 마라. 모르면 null 로 둬라.\n"
        "- 한국어 사용자에게도 영어 원문 자료 추천이 자연스럽다. 단, reason_short_ko 는 "
        "반드시 한국어.\n"
        "- 동일 publisher 가 한 슬롯에서 2개 이상 나오지 않도록.\n\n"
        "응답 형식: {\"items\": [...10개...]}"
    )
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user_content),
    ]


def _is_valid_url(text: str | None) -> bool:
    """간단 URL 검증 — http(s) prefix."""
    if not text:
        return False
    lower = text.lower().strip()
    return lower.startswith(("http://", "https://"))


def _validate_cold_start(parsed: Any) -> list[ColdStartItem]:
    """cold-start.md §응답 검증 그대로.

    - 10개 정확
    - slot 분배 5/3/2
    - title 필수
    - url_hint 가 있되 invalid 면 None 으로 강등 (LLM 환상 차단)
    - reason_short_ko ≤80자
    """
    if not isinstance(parsed, dict):
        raise InvalidColdStartResponse(
            ErrorCode.COLD_START_LLM_FAILED, "LLM response not JSON object"
        )
    raw_items = parsed.get("items")
    if not isinstance(raw_items, list):
        raise InvalidColdStartResponse(
            ErrorCode.COLD_START_LLM_FAILED, "items field missing or not list"
        )
    if len(raw_items) != 10:
        raise InvalidColdStartResponse(
            ErrorCode.COLD_START_LLM_FAILED,
            f"expected 10 items, got {len(raw_items)}",
        )
    slot_counts: dict[SlotType, int] = {
        SlotType.CORE: 0,
        SlotType.ADJACENT: 0,
        SlotType.DISCOVERY: 0,
    }
    items: list[ColdStartItem] = []
    for i, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise InvalidColdStartResponse(
                ErrorCode.COLD_START_LLM_FAILED, f"item[{i}] not object"
            )
        slot_str = raw.get("slot_type")
        if slot_str not in ("core", "adjacent", "discovery"):
            raise InvalidColdStartResponse(
                ErrorCode.COLD_START_LLM_FAILED,
                f"item[{i}] invalid slot_type: {slot_str!r}",
            )
        slot = SlotType(slot_str)
        slot_counts[slot] += 1
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            raise InvalidColdStartResponse(
                ErrorCode.COLD_START_LLM_FAILED, f"item[{i}] missing title"
            )
        source_type = raw.get("source_type", "vendor_blog")
        if source_type not in _SOURCE_TYPE_VALID:
            source_type = "vendor_blog"
        reason = raw.get("reason_short_ko", "")
        if not isinstance(reason, str):
            reason = ""
        reason = reason.strip()
        if len(reason) > _REASON_MAX_LENGTH:
            # truncate + warning — 거부 대신 절단 (cold-start LLM 환상 완화).
            reason = reason[: _REASON_MAX_LENGTH - 1] + "…"
        url_hint_raw = raw.get("url_hint")
        url_hint = (
            url_hint_raw
            if isinstance(url_hint_raw, str) and _is_valid_url(url_hint_raw)
            else None
        )
        doi_hint_raw = raw.get("doi_hint")
        doi_hint = (
            doi_hint_raw
            if isinstance(doi_hint_raw, str) and doi_hint_raw.strip()
            else None
        )
        year_raw = raw.get("published_year")
        published_year = year_raw if isinstance(year_raw, int) else None
        related_csos_raw = raw.get("related_csos_en", [])
        related_csos = (
            [s for s in related_csos_raw if isinstance(s, str)]
            if isinstance(related_csos_raw, list)
            else []
        )
        items.append(
            ColdStartItem(
                slot_type=slot,
                title=title.strip(),
                title_ko=(
                    raw["title_ko"]
                    if isinstance(raw.get("title_ko"), str)
                    else None
                ),
                publisher_domain=(
                    raw["publisher_domain"]
                    if isinstance(raw.get("publisher_domain"), str)
                    else None
                ),
                publisher_label=(
                    raw["publisher_label"]
                    if isinstance(raw.get("publisher_label"), str)
                    else None
                ),
                source_type=source_type,
                url_hint=url_hint,
                doi_hint=doi_hint,
                published_year=published_year,
                related_csos_en=related_csos,
                reason_short_ko=reason,
            )
        )
    if slot_counts != _SLOT_DISTRIBUTION:
        raise InvalidColdStartResponse(
            ErrorCode.COLD_START_LLM_FAILED,
            f"slot distribution mismatch: got {slot_counts}, expected 5/3/2",
        )
    return items


async def _count_cold_start_attempts(
    db: AsyncSession, user_id: UUID
) -> int:
    """사용자의 cold-start 시도 횟수 — content_type='pseudo_cold_start' Recommendation row."""
    stmt = (
        select(func.count(Recommendation.recommendation_id.distinct()))
        .join(Document, Document.document_id == Recommendation.document_id)
        .where(
            Recommendation.user_id == user_id,
            Document.content_type == ContentType.PSEUDO_COLD_START.value,
        )
    )
    return (await db.execute(stmt)).scalar_one() or 0


async def _acquire_user_attempt_lock(db: AsyncSession, user_id: UUID) -> None:
    """사용자별 cold-start count+insert 구간을 PostgreSQL transaction lock 으로 직렬화."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"cold_start:{user_id}"},
    )


async def _get_sentinel_source_id(db: AsyncSession) -> UUID:
    """sentinel `cold_start_pseudo` source_id lookup. alembic 0001 시드."""
    stmt = select(Source.source_id).where(
        Source.name == SentinelSource.COLD_START_PSEUDO_NAME
    )
    sid = (await db.execute(stmt)).scalar_one_or_none()
    if sid is None:
        raise RuntimeError(
            f"sentinel source '{SentinelSource.COLD_START_PSEUDO_NAME}' missing — "
            "alembic 0001 미시드 또는 잘못된 schema"
        )
    return sid


async def _resolve_cluster_labels(
    db: AsyncSession, cluster_ids: Iterable[UUID]
) -> list[str]:
    """BroadInterest.broad_interest_id → cluster_label list. LLM input 용."""
    ids = list(cluster_ids)
    if not ids:
        return []
    stmt = select(BroadInterest.cso_cluster_label).where(
        BroadInterest.broad_interest_id.in_(ids)
    )
    return [str(label) for label in (await db.execute(stmt)).scalars().all()]


async def _resolve_cluster_seed_topic_ids(
    db: AsyncSession, cluster_ids: Iterable[UUID]
) -> list[UUID]:
    """BroadInterest.broad_interest_id → BroadInterest.cso_seed_topic_id list.

    cold_start 의 pseudo Document → DocumentTopic 매핑 시 사용. A8 trace creation
    hook 이 `_document_topic_cso_ids` 로 cso_id 추출 — 본 매핑이 있어야 첫 click
    시 새 trace 자동 생성 동작 (A7 결정 #6 plan TBD 완성 정합).

    cluster_ids 순서 보존 — slot 별 round-robin 매핑 가능.
    """
    ids = list(cluster_ids)
    if not ids:
        return []
    stmt = select(
        BroadInterest.broad_interest_id, BroadInterest.cso_seed_topic_id
    ).where(BroadInterest.broad_interest_id.in_(ids))
    seed_by_id: dict[UUID, UUID] = {}
    for row in await db.execute(stmt):
        seed_by_id[row.broad_interest_id] = row.cso_seed_topic_id
    # input 순서 보존
    return [seed_by_id[cid] for cid in ids if cid in seed_by_id]


async def _lookup_existing_document(
    db: AsyncSession,
    *,
    canonical_url: str | None,
    doi: str | None,
) -> UUID | None:
    """canonical_url → doi 순 lookup (A4 _lookup_existing_document_id 패턴 응용)."""
    if canonical_url:
        stmt = select(Document.document_id).where(
            Document.canonical_url == canonical_url
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
    if doi:
        stmt = select(Document.document_id).where(Document.doi == doi)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
    return None


async def _insert_pseudo_document(
    db: AsyncSession,
    *,
    sentinel_source_id: UUID,
    item: ColdStartItem,
    request_id: str,
    item_idx: int,
    model_used: str,
    default_cso_topic_id: UUID | None = None,
) -> UUID:
    """pseudo Document INSERT — sentinel source + content_type='pseudo_cold_start'.

    url_hint 매칭 시 기존 Document 재사용 (A4 dedup 패턴).
    untargeted on_conflict_do_nothing — race 시 lookup fallback.

    (R3 시연 발견 결함 fix, 2026-05-17) `default_cso_topic_id` 가 주어지면 신규 INSERT
    한 Document 에 대해 DocumentTopic 도 함께 INSERT — 트레이스 자동 생성 hook
    (`_document_topic_cso_ids`) 가 cso_id 추출 가능. user 선택 cluster 의
    `cso_seed_topic_id` 가 default (slot 별 round-robin 또는 동일 cluster).
    confidence=0.5 (cold-start LLM 추정 신뢰도).
    """
    existing = await _lookup_existing_document(
        db, canonical_url=item.url_hint, doi=item.doi_hint
    )
    if existing is not None:
        # 기존 Document — DocumentTopic 이미 있을 가능성. on_conflict_do_nothing 으로 안전.
        if default_cso_topic_id is not None:
            await _upsert_pseudo_document_topic(
                db, existing, default_cso_topic_id
            )
        return existing
    new_id = uuid4()
    url = item.url_hint or f"internal://cold-start/{request_id}/{item_idx}"
    published_at = (
        datetime(item.published_year, 1, 1, tzinfo=UTC)
        if item.published_year
        else None
    )
    stmt = (
        pg_insert(Document)
        .values(
            document_id=new_id,
            source_id=sentinel_source_id,
            title=item.title,
            normalized_title=normalize_title(item.title),
            url=url,
            canonical_url=item.url_hint,
            doi=item.doi_hint,
            summary=item.title_ko or item.title,
            published_at=published_at,
            content_type=ContentType.PSEUDO_COLD_START.value,
            language="en",
            raw={
                "publisher_domain": item.publisher_domain,
                "publisher_label": item.publisher_label,
                "title_ko": item.title_ko,
                "related_csos_en": item.related_csos_en,
                "llm_meta": {
                    "cold_start_request_id": request_id,
                    "model_used": model_used,
                    "source_type_hint": item.source_type,
                },
            },
        )
        .on_conflict_do_nothing()
        .returning(Document.document_id)
    )
    result = await db.execute(stmt)
    inserted = result.scalar_one_or_none()
    if inserted is not None:
        # 신규 INSERT 성공 — DocumentTopic 도 매핑 (trace creation hook 정합).
        if default_cso_topic_id is not None:
            await _upsert_pseudo_document_topic(
                db, inserted, default_cso_topic_id
            )
        return inserted
    # race — 재 lookup.
    fallback = await _lookup_existing_document(
        db, canonical_url=item.url_hint, doi=item.doi_hint
    )
    if fallback is not None:
        if default_cso_topic_id is not None:
            await _upsert_pseudo_document_topic(
                db, fallback, default_cso_topic_id
            )
        return fallback
    raise InvalidColdStartResponse(
        ErrorCode.COLD_START_LLM_FAILED,
        f"failed to persist pseudo Document for item[{item_idx}]",
    )


async def _upsert_pseudo_document_topic(
    db: AsyncSession, document_id: UUID, cso_topic_id: UUID
) -> None:
    """pseudo Document → DocumentTopic 매핑 INSERT (race-safe).

    partial UNIQUE `ux_document_topic_cso_only` (WHERE leaf IS NULL) 매칭 —
    동일 (document_id, cso_topic_id, leaf=NULL) 가 이미 있으면 skip.

    confidence=0.5 (cold-start LLM 추정 신뢰도, 일반 검색 0.8~0.9 보다 낮음).
    """
    stmt = (
        pg_insert(DocumentTopic)
        .values(
            id=uuid4(),
            document_id=document_id,
            cso_topic_id=cso_topic_id,
            leaf_topic_id=None,
            confidence=0.5,
        )
        .on_conflict_do_nothing(
            index_elements=["document_id", "cso_topic_id"],
            index_where=(
                DocumentTopic.leaf_topic_id.is_(None)
                & DocumentTopic.cso_topic_id.is_not(None)
            ),
        )
    )
    await db.execute(stmt)


# Lua atomic INCR + EXPIRE — A6 dwell cap 패턴 (R2 Suggested #1 fix).
# INCR 성공 후 EXPIRE 전 crash 시 TTL 없는 영구 key 잔존 race 차단.
_DAILY_CAP_INCR_LUA = """
local v = redis.call('INCR', KEYS[1])
if v == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return v
"""


async def _check_global_daily_cap(
    redis: aioredis.Redis, settings: Settings
) -> None:
    """전역 일 cap (COLD_START_MAX_PER_DAY). Lua atomic INCR + EXPIRE 86400.

    R2 Suggested #1 fix: INCR + EXPIRE 분리 패턴은 INCR 성공 후 EXPIRE 전 crash 시
    TTL 없는 영구 key 잔존. A6 dwell_tick Lua atomic 패턴으로 교체.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    daily_key = f"cold_start:daily:{today}"
    count_raw = await redis.eval(_DAILY_CAP_INCR_LUA, 1, daily_key, 86400)  # type: ignore[misc]
    count = int(count_raw)
    if count > settings.COLD_START_MAX_PER_DAY:
        raise ColdStartCapExceeded(
            ErrorCode.COLD_START_LLM_FAILED,
            f"daily cap exceeded: {count} > {settings.COLD_START_MAX_PER_DAY}",
        )


async def _set_status(
    redis: aioredis.Redis,
    request_id: UUID,
    *,
    status: str,
    progress_percent: int = 0,
    error_code: str | None = None,
    dashboard_ready: bool = False,
) -> None:
    """Redis HSET cold_start_status:{request_id} 상태 갱신."""
    mapping: dict[str, str] = {
        "status": status,
        "progress_percent": str(progress_percent),
        "dashboard_ready": "true" if dashboard_ready else "false",
    }
    if error_code is not None:
        mapping["error_code"] = error_code
    if status in ("completed", "failed"):
        mapping["completed_at"] = datetime.now(UTC).isoformat()
    await redis.hset(  # type: ignore[misc]
        RedisKey.cold_start_status(request_id), mapping=mapping
    )


def _cold_start_lock_ttl_seconds(settings: Settings) -> int:
    """LLM timeout 보다 긴 worker-owned onboarding lock TTL."""
    return max(settings.COLD_START_LLM_TIMEOUT_SECONDS + 120, 300)


async def _acquire_cold_start_lock(
    redis: aioredis.Redis,
    settings: Settings,
    *,
    user_id: UUID,
    request_id: UUID,
) -> bool:
    """onboarding lock 을 worker request_id 소유로 연장해 중복 cold-start 를 차단."""
    lock_key = RedisKey.onboarding_lock(user_id)
    request_key = f"{lock_key}:request_id"
    result = await redis.eval(  # type: ignore[misc]
        _ACQUIRE_ONBOARDING_LOCK_LUA,
        2,
        lock_key,
        request_key,
        str(request_id),
        _cold_start_lock_ttl_seconds(settings),
    )
    return int(result) == 1


async def _release_cold_start_lock(
    redis: aioredis.Redis,
    *,
    user_id: UUID,
    request_id: UUID,
) -> None:
    """worker 가 소유한 onboarding lock 만 CAS 로 해제."""
    lock_key = RedisKey.onboarding_lock(user_id)
    request_key = f"{lock_key}:request_id"
    await redis.eval(  # type: ignore[misc]
        _RELEASE_ONBOARDING_LOCK_LUA,
        2,
        lock_key,
        request_key,
        str(request_id),
    )


async def _persist_cold_start_recommendations(
    db: AsyncSession,
    *,
    user_id: UUID,
    items: list[ColdStartItem],
    doc_ids: list[UUID],
) -> dict[UUID, UUID]:
    """Recommendation x10 + RecommendationSlot x3 INSERT.

    cold-start 카드는 score=0.0 (ranking 미수행 — LLM 직접 생성).
    daily UNIQUE 충돌 시 skip (§11.#2 방어).
    """
    doc_to_rec_id: dict[UUID, UUID] = {}
    slot_counts: dict[SlotType, int] = {
        SlotType.CORE: 0,
        SlotType.ADJACENT: 0,
        SlotType.DISCOVERY: 0,
    }
    for item, doc_id in zip(items, doc_ids, strict=True):
        new_rec_id = uuid4()
        stmt = (
            pg_insert(Recommendation)
            .values(
                recommendation_id=new_rec_id,
                user_id=user_id,
                document_id=doc_id,
                slot_type=item.slot_type.value,
                reason=item.reason_short_ko[:255] if item.reason_short_ko else None,
                score=0.0,
            )
            .on_conflict_do_nothing()
            .returning(Recommendation.recommendation_id)
        )
        result = await db.execute(stmt)
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            doc_to_rec_id[doc_id] = inserted
            slot_counts[item.slot_type] += 1
        else:
            # race — 기존 lookup.
            lookup = (
                await db.execute(
                    select(Recommendation.recommendation_id)
                    .where(
                        Recommendation.user_id == user_id,
                        Recommendation.document_id == doc_id,
                        Recommendation.slot_type == item.slot_type.value,
                        func.date(
                            func.timezone("UTC", Recommendation.created_at)
                        )
                        == func.date(func.timezone("UTC", func.now())),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if lookup is not None:
                doc_to_rec_id[doc_id] = lookup
                slot_counts[item.slot_type] += 1
    # RecommendationSlot x3 — target 5/3/2.
    targets = {SlotType.CORE: 5, SlotType.ADJACENT: 3, SlotType.DISCOVERY: 2}
    for slot, target in targets.items():
        db.add(
            RecommendationSlot(
                slot_id=uuid4(),
                user_id=user_id,
                slot_type=slot.value,
                target_count=target,
                actual_count=slot_counts[slot],
                fallback_reason=None,
            )
        )
    return doc_to_rec_id


async def run_cold_start(
    session_factory: async_sessionmaker[AsyncSession],
    redis: aioredis.Redis,
    provider: LLMProvider,
    settings: Settings,
    *,
    request_id: str,
    user_id: str,
    cluster_ids: list[str],
    user_class: str,
    locale: str,
) -> None:
    """cold-start 메인. worker/jobs/cold_start.py 의 asyncio.run 안에서 호출.

    별도 DB session — worker context (request session 외부).
    """
    request_uuid = UUID(request_id)
    user_uuid = UUID(user_id)
    cluster_uuids = [UUID(c) for c in cluster_ids]
    try:
        user_class_enum = UserClass(user_class)
    except ValueError:
        user_class_enum = UserClass.GENERAL

    try:
        await _set_status(redis, request_uuid, status="running", progress_percent=10)
    except Exception:
        logger.warning("cold_start: failed to set initial status request=%s", request_id)
        return

    try:
        lock_acquired = await _acquire_cold_start_lock(
            redis, settings, user_id=user_uuid, request_id=request_uuid
        )
    except Exception:
        logger.warning("cold_start: failed to acquire user lock request=%s", request_id)
        lock_acquired = False
    if not lock_acquired:
        await _set_status(
            redis,
            request_uuid,
            status="failed",
            progress_percent=100,
            error_code=ErrorCode.COLD_START_IN_PROGRESS.value,
        )
        return

    async with session_factory() as session:
        try:
            # 1. cap 검증.
            user = await session.get(User, user_uuid)
            if user is None:
                raise InvalidColdStartResponse(
                    ErrorCode.COLD_START_LLM_FAILED, f"user not found: {user_uuid}"
                )
            await _acquire_user_attempt_lock(session, user_uuid)
            attempts = await _count_cold_start_attempts(session, user_uuid)
            if attempts >= settings.COLD_START_MAX_PER_USER_LIFETIME:
                raise ColdStartCapExceeded(
                    ErrorCode.COLD_START_LLM_FAILED,
                    f"user lifetime cap exceeded: {attempts}",
                )
            await _check_global_daily_cap(redis, settings)

            # 2. LLM 호출.
            cluster_labels = await _resolve_cluster_labels(session, cluster_uuids)
            messages = _build_cold_start_prompt(
                cluster_labels, user_class_enum, locale
            )
            await _set_status(
                redis, request_uuid, status="running", progress_percent=30
            )
            async with acquire_slot(user_uuid):
                async with asyncio.timeout(
                    settings.COLD_START_LLM_TIMEOUT_SECONDS
                ):
                    llm_resp = await provider.complete(
                        messages,
                        model_slot="high",
                        response_format="json",
                        user_id=str(user_uuid),
                    )
            await _set_status(
                redis, request_uuid, status="running", progress_percent=60
            )

            # 3. validate.
            items = _validate_cold_start(llm_resp.parsed_json)

            # 4. pseudo Document INSERT.
            # (R3 시연 발견 fix, 2026-05-17) cluster_seed_topic_ids 추출 — DocumentTopic
            # 매핑에 사용. user 가 선택한 cluster 의 cso_seed_topic_id round-robin.
            # 빈 list (cluster_uuids=[]) 시 default_cso=None → DocumentTopic 매핑 skip.
            cluster_seeds = await _resolve_cluster_seed_topic_ids(
                session, cluster_uuids
            )
            sentinel_id = await _get_sentinel_source_id(session)
            doc_ids: list[UUID] = []
            for idx, item in enumerate(items):
                # round-robin: 0→cluster[0], 1→cluster[1], 2→cluster[2], 3→cluster[0]...
                default_cso = (
                    cluster_seeds[idx % len(cluster_seeds)]
                    if cluster_seeds
                    else None
                )
                doc_id = await _insert_pseudo_document(
                    session,
                    sentinel_source_id=sentinel_id,
                    item=item,
                    request_id=request_id,
                    item_idx=idx,
                    model_used=llm_resp.model,
                    default_cso_topic_id=default_cso,
                )
                doc_ids.append(doc_id)
            await _set_status(
                redis, request_uuid, status="running", progress_percent=85
            )

            # 5. Recommendation + RecommendationSlot.
            await _persist_cold_start_recommendations(
                session, user_id=user_uuid, items=items, doc_ids=doc_ids
            )

            # 6. db.commit 성공 후 → cache SET + status=completed (§11.#1 회피).
            await session.commit()
        except (TimeoutError, ColdStartCapExceeded, InvalidColdStartResponse, ProviderError, LLMBudgetExceeded) as exc:
            await session.rollback()
            code = (
                exc.error_code.value
                if isinstance(exc, ColdStartFailed)
                else ErrorCode.COLD_START_LLM_FAILED.value
            )
            logger.warning(
                "cold_start failed request=%s code=%s err=%s",
                request_id,
                code,
                exc,
            )
            try:
                await _set_status(
                    redis,
                    request_uuid,
                    status="failed",
                    progress_percent=100,
                    error_code=code,
                )
            except Exception:
                logger.exception("cold_start: failed to set failed status")
            try:
                await _release_cold_start_lock(
                    redis, user_id=user_uuid, request_id=request_uuid
                )
            except Exception:
                logger.exception("cold_start: failed to release user lock")
            return
        except Exception as exc:
            await session.rollback()
            logger.exception("cold_start unexpected failure request=%s", request_id)
            try:
                await _set_status(
                    redis,
                    request_uuid,
                    status="failed",
                    progress_percent=100,
                    error_code=ErrorCode.COLD_START_LLM_FAILED.value,
                )
            except Exception:
                logger.exception("cold_start: failed to set failed status")
            try:
                await _release_cold_start_lock(
                    redis, user_id=user_uuid, request_id=request_uuid
                )
            except Exception:
                logger.exception("cold_start: failed to release user lock")
            _ = exc
            return

    # 7. cache SET — db.commit 성공 후 (§11.#1 cache-before-commit 회피).
    # response 는 다음 GET /recommendations/dashboard 가 build (cold-start 로딩 경로).
    try:
        await _set_status(
            redis,
            request_uuid,
            status="completed",
            progress_percent=100,
            dashboard_ready=True,
        )
    except Exception:
        logger.warning("cold_start: failed to set completed status request=%s", request_id)
    try:
        await _release_cold_start_lock(
            redis, user_id=user_uuid, request_id=request_uuid
        )
    except Exception:
        logger.warning("cold_start: failed to release user lock request=%s", request_id)


__all__ = [
    "ColdStartCapExceeded",
    "ColdStartFailed",
    "ColdStartItem",
    "InvalidColdStartResponse",
    "run_cold_start",
]
