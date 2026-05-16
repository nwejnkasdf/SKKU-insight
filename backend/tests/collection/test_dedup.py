"""collection.dedup unit tests — 순수 함수, DB/Redis 의존 없음.

dedup 우선순위 룰: DOI → canonical_url → normalized_url → title Levenshtein ≥ 0.90.

(v13 round 2 Codex C-02) collapse 는 (to_insert, to_link) 튜플 반환. existing 의
document_id 가 매칭되면 candidate 를 매핑-only 그룹으로 분류.
"""
from __future__ import annotations

from uuid import uuid4

from app.collection import dedup
from app.llm_provider.protocol import SearchResult


def _result(
    *,
    title: str = "Test Paper",
    url: str = "https://example.com/paper",
    canonical_url: str | None = None,
    doi: str | None = None,
) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        canonical_url=canonical_url,
        doi=doi,
        abstract_summary="요약",
    )


class TestNormalizeUrl:
    def test_strips_utm_params(self) -> None:
        out = dedup.normalize_url(
            "https://example.com/x?utm_source=tw&utm_medium=share&q=hi"
        )
        assert "utm_source" not in out
        assert "utm_medium" not in out
        assert "q=hi" in out

    def test_strips_fbclid_gclid(self) -> None:
        out = dedup.normalize_url(
            "https://example.com/x?fbclid=AA&gclid=BB&keep=1"
        )
        assert "fbclid" not in out
        assert "gclid" not in out
        assert "keep=1" in out

    def test_lowercases_host(self) -> None:
        out = dedup.normalize_url("https://Example.COM/Path/CaseSensitive")
        assert out.startswith("https://example.com")
        # path case 보존
        assert "/Path/CaseSensitive" in out

    def test_removes_fragment(self) -> None:
        out = dedup.normalize_url("https://example.com/x#section")
        assert "#" not in out

    def test_trailing_slash_removed(self) -> None:
        assert dedup.normalize_url("https://example.com/foo/") == "https://example.com/foo"
        # root "/" 는 보존
        assert dedup.normalize_url("https://example.com/").endswith("/")

    def test_empty_input(self) -> None:
        assert dedup.normalize_url("") == ""


class TestNormalizeTitle:
    def test_lowercase_and_nfkc(self) -> None:
        # full-width 'Ａ' → 'a'
        out = dedup.normalize_title("Ｔｉｔｌｅ")
        assert out == "title"

    def test_collapses_whitespace(self) -> None:
        out = dedup.normalize_title("  hello   world  ")
        assert out == "hello world"

    def test_strips_punctuation(self) -> None:
        out = dedup.normalize_title("Hello, World! — Sub-title.")
        # 구두점이 공백으로 바뀌고 collapse 됨
        assert "," not in out
        assert "!" not in out
        assert "hello" in out
        assert "world" in out
        assert "sub" in out and "title" in out


class TestIsTitleDuplicate:
    def test_threshold_above_returns_true(self) -> None:
        a = dedup.normalize_title("Transformer architecture for language models")
        b = dedup.normalize_title("Transformer architectures for language models")
        assert dedup.is_title_duplicate(a, b)

    def test_below_threshold_returns_false(self) -> None:
        a = dedup.normalize_title("Quantum cryptography overview")
        b = dedup.normalize_title("Blockchain consensus mechanisms")
        assert not dedup.is_title_duplicate(a, b)

    def test_short_titles_require_exact_match(self) -> None:
        # length < 8 인 경우 exact match 만 인정 (false positive 회피)
        assert dedup.is_title_duplicate("AI", "AI")
        assert not dedup.is_title_duplicate("AI", "ML")


class TestCollapse:
    def test_doi_priority_links_to_existing(self) -> None:
        existing_id = uuid4()
        existing = [
            dedup.make_key(_result(doi="10.1234/x"), document_id=existing_id)
        ]
        candidates = [
            _result(title="다른 제목 같은 DOI", doi="10.1234/X"),  # case-insensitive
        ]
        to_insert, to_link = dedup.collapse(existing, candidates)
        assert to_insert == []
        assert len(to_link) == 1
        assert to_link[0][1] == existing_id

    def test_canonical_url_priority_links(self) -> None:
        existing_id = uuid4()
        existing = [
            dedup.make_key(
                _result(canonical_url="https://a.com/p"), document_id=existing_id
            )
        ]
        candidates = [
            _result(title="다른 제목", canonical_url="https://A.COM/p"),
        ]
        to_insert, to_link = dedup.collapse(existing, candidates)
        assert to_insert == []
        assert to_link[0][1] == existing_id

    def test_normalized_url_priority_links(self) -> None:
        existing_id = uuid4()
        existing = [
            dedup.make_key(
                _result(url="https://a.com/p?utm_source=x"), document_id=existing_id
            )
        ]
        candidates = [_result(title="t1", url="https://a.com/p?utm_source=y")]
        to_insert, to_link = dedup.collapse(existing, candidates)
        assert to_insert == []
        assert to_link[0][1] == existing_id

    def test_title_levenshtein_priority(self) -> None:
        existing_id = uuid4()
        existing = [
            dedup.make_key(
                _result(title="Transformer architecture for language models"),
                document_id=existing_id,
            )
        ]
        candidates = [
            _result(
                title="Transformer architectures for language models",
                url="https://different.com/p",
            )
        ]
        to_insert, to_link = dedup.collapse(existing, candidates)
        assert to_insert == []
        assert to_link[0][1] == existing_id

    def test_existing_without_document_id_skips_candidate(self) -> None:
        # document_id 가 None 인 existing 과 매칭되면 매핑 불가 → candidate 무시
        existing = [dedup.make_key(_result(doi="10.1234/x"), document_id=None)]
        candidates = [_result(title="t1", doi="10.1234/x")]
        to_insert, to_link = dedup.collapse(existing, candidates)
        assert to_insert == []
        assert to_link == []

    def test_internal_dedup_within_candidates(self) -> None:
        # 같은 batch 내부 중복: 첫 번째만 to_insert, 두 번째는 (PK 미정으로) 무시
        candidates = [
            _result(doi="10.1234/x", title="t1"),
            _result(doi="10.1234/x", title="t2", url="https://other.com"),
            _result(doi="10.5555/y", title="t3"),
        ]
        to_insert, to_link = dedup.collapse([], candidates)
        assert len(to_insert) == 2
        assert to_insert[0].doi == "10.1234/x"
        assert to_insert[1].doi == "10.5555/y"
        assert to_link == []
