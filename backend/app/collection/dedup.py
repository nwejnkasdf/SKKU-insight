"""문서 중복 제거 룰 — v13 라운드 A4 사용자 결정 매트릭스.

우선순위 (prompts/03-A4-collection.md §Dedup 우선순위):
  1. DOI 정확 일치 (case-insensitive, https://doi.org/ 접두어 제거)
  2. canonical_url 정확 일치 (normalize 후)
  3. URL 정규화 (utm_*/fbclid/gclid 제거 + lowercase host + fragment 제거)
  4. title 정규화 + rapidfuzz Levenshtein ratio ≥ 0.90 (normalized 길이 ≥ 8 일 때만)

순수 함수 — DB 호출 X. orchestrator 가 existing dedup key set 을 사전 적재 후 본 함수
들의 결과로 신규 INSERT vs 기존 DocumentTopic upsert 분기 결정. 동일 batch 내부 (LLM
1회 응답 안) 중복도 catch.

(v13 round 2 Codex C-02 fix, 2026-05-16):
- `DedupKey` 에 `document_id` 추가 — 매칭된 기존 Document 의 PK 반환.
- `collapse()` 가 `(신규 INSERT 대상, 기존 매핑 대상)` 튜플 반환 → orchestrator 가 두
  그룹 분기 처리해 토픽-문서 엣지 누락 (Codex C-02) 방지.

Levenshtein 라이브러리: rapidfuzz (BSD3, C++ 구현, ~500KB wheel).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qsl, urlparse, urlunparse
from uuid import UUID

from rapidfuzz import fuzz

from app.llm_provider.protocol import SearchResult

# 제거할 tracking query param 접두/완전일치 set.
_TRACKING_PARAM_EXACT: Final[frozenset[str]] = frozenset(
    {"fbclid", "gclid", "igshid", "msclkid", "yclid", "ref", "source"}
)
_TRACKING_PARAM_PREFIXES: Final[tuple[str, ...]] = ("utm_",)

_TITLE_DUPLICATE_THRESHOLD: Final[float] = 0.90
_TITLE_MIN_LENGTH_FOR_FUZZY: Final[int] = 8

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
_PUNCTUATION_RE: Final[re.Pattern[str]] = re.compile(
    r"[\.,;:!\?\-—_'\"\(\)\[\]\{\}<>·•/\\]+"
)


def normalize_url(url: str) -> str:
    """tracking 파라미터 제거 + lowercase host + fragment 제거 + trailing slash 정리.

    스킴/포트 보존. 빈 입력은 빈 문자열 반환.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip()
    # query 필터링
    kept_params = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        lower_k = k.lower()
        if lower_k in _TRACKING_PARAM_EXACT:
            continue
        if any(lower_k.startswith(prefix) for prefix in _TRACKING_PARAM_PREFIXES):
            continue
        kept_params.append((k, v))
    # 순서 보존 직렬화 (정렬은 dedup safe but 의미 정보 손실 우려 → 그대로 유지)
    new_query = "&".join(f"{k}={v}" if v else k for k, v in kept_params)
    # host lowercase. fragment 제거. trailing slash 는 path 가 "/" 단독이 아닐 때만 제거.
    new_netloc = parsed.netloc.lower()
    new_path = parsed.path
    if new_path.endswith("/") and new_path != "/":
        new_path = new_path[:-1]
    return urlunparse(
        (parsed.scheme.lower(), new_netloc, new_path, parsed.params, new_query, "")
    )


def normalize_title(title: str) -> str:
    """lowercase + Unicode NFKC + 공백 collapse + 구두점 제거."""
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", title)
    text = text.lower()
    text = _PUNCTUATION_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def _normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    cleaned = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    return cleaned or None


def is_title_duplicate(
    a_norm: str,
    b_norm: str,
    *,
    threshold: float = _TITLE_DUPLICATE_THRESHOLD,
) -> bool:
    """rapidfuzz.fuzz.ratio 기반. normalized 길이 ≥ 8 일 때만 활성 (짧은 제목 false-positive 회피)."""
    if not a_norm or not b_norm:
        return False
    if len(a_norm) < _TITLE_MIN_LENGTH_FOR_FUZZY or len(b_norm) < _TITLE_MIN_LENGTH_FOR_FUZZY:
        return a_norm == b_norm
    ratio = fuzz.ratio(a_norm, b_norm) / 100.0
    return ratio >= threshold


@dataclass(slots=True, frozen=True)
class DedupKey:
    """단일 Document 의 dedup 비교 키 + PK.

    (v13 round 2 Codex C-02 fix) `document_id` 추가 — 매칭된 기존 Document 의 PK 를
    orchestrator 에 돌려줘서 DocumentTopic 만 upsert 가능하게.
    candidate 측 (load_existing_dedup_keys 이외) 도 동일 dataclass 사용 가능하지만
    candidate.document_id 는 의미 없음 (orchestrator 가 INSERT 후 set).
    """

    document_id: UUID | None
    doi: str | None
    canonical_url: str | None
    normalized_url: str
    normalized_title: str


def make_key(result: SearchResult, *, document_id: UUID | None = None) -> DedupKey:
    """SearchResult → DedupKey 변환. orchestrator 가 INSERT 직전·직후 호출.

    `document_id` 는 기존 Document 일 때 채움 (load_existing_dedup_keys).
    신규 candidate 는 None — collapse 가 None 인 키를 매핑-only 분기로 사용 X.
    """
    return DedupKey(
        document_id=document_id,
        doi=_normalize_doi(result.doi),
        canonical_url=normalize_url(result.canonical_url) if result.canonical_url else None,
        normalized_url=normalize_url(result.url),
        normalized_title=normalize_title(result.title),
    )


def _is_duplicate(existing: DedupKey, candidate: DedupKey) -> bool:
    """우선순위 룰. 하나라도 일치하면 중복."""
    # 1) DOI
    if existing.doi and candidate.doi and existing.doi == candidate.doi:
        return True
    # 2) canonical_url
    if (
        existing.canonical_url
        and candidate.canonical_url
        and existing.canonical_url == candidate.canonical_url
    ):
        return True
    # 3) normalized url
    if (
        existing.normalized_url
        and candidate.normalized_url
        and existing.normalized_url == candidate.normalized_url
    ):
        return True
    # 4) title Levenshtein
    if is_title_duplicate(existing.normalized_title, candidate.normalized_title):
        return True
    return False


def collapse(
    existing: Iterable[DedupKey],
    candidates: Iterable[SearchResult],
) -> tuple[list[SearchResult], list[tuple[SearchResult, UUID]]]:
    """existing 과의 중복 + 후보 내부 중복을 한 번에 분리.

    (v13 round 2 Codex C-02 fix) 반환 형식 변경 — 단일 list 가 아닌 튜플:
    - `to_insert`: existing 과 매칭 안 된 신규 SearchResult. Document INSERT + DocumentTopic INSERT.
    - `to_link`: 매칭 된 (SearchResult, 기존 document_id) 튜플 list. DocumentTopic 만 upsert.

    existing 의 document_id 가 None (alembic 시드 등 inferred) 인 매칭은 `to_link` 에
    포함 안 됨 (PK 없으면 매핑 INSERT 불가) — 해당 candidate 는 통째로 무시.

    내부 중복: 같은 batch 안 두 candidate 가 매칭하면 둘 중 첫 번째만 to_insert,
    두 번째는 (방금 INSERT 될 document 의 PK 미정이라) 무시 — orchestrator 가
    persist 후 existing 에 추가하므로 다음 leaf 부터는 정상 to_link 됨.

    반환 list 순서는 입력 candidates 순서 보존.
    """
    seen: list[DedupKey] = list(existing)
    to_insert: list[SearchResult] = []
    to_link: list[tuple[SearchResult, UUID]] = []
    for candidate in candidates:
        c_key = make_key(candidate)
        matched: DedupKey | None = None
        for s in seen:
            if _is_duplicate(s, c_key):
                matched = s
                break
        if matched is None:
            to_insert.append(candidate)
            seen.append(c_key)
        elif matched.document_id is not None:
            to_link.append((candidate, matched.document_id))
        # matched 가 있지만 document_id None → 매핑 불가, candidate skip
    return to_insert, to_link


__all__ = [
    "DedupKey",
    "collapse",
    "is_title_duplicate",
    "make_key",
    "normalize_title",
    "normalize_url",
]
