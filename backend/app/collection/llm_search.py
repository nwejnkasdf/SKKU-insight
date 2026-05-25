"""LLM tool-use 검색 wrapper — v13 라운드 A4 Topic-driven Pivot.

prompts/03-A4-collection.md §LLM 호출 패턴 정합:
- 1 call / 1 active leaf
- top_n=10 결과
- user trace JSON + leaf_label 입력 → LLM 자율 query 결정
- SearchResult list 출력 (Document INSERT 직전 변환)

NFR-25 정합: prompt 의 §자가 요약 instruction 으로 외부 abstract 직접 복제 차단.
Document.summary 는 LLM self-summary (본인 말 1~2문장, ≤200자).

import-time assertion 으로 prompt 의 핵심 instruction 키워드 보장 — 누군가 prompt 를
수정해 NFR-25 instruction 을 제거하면 모듈 import 단계에서 즉시 실패 (정적 guard).
audit regression test 가 같은 키워드를 검증.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.llm_provider.protocol import LLMProvider, SearchResult

# (Codex round 2 N-03) prompt 버전 — hash_prompt_search 가 포함해 fixture 자동 invalidate.
# prompt 의미 변경 시 본 상수 bump → MockProvider 의 search_{hash}.json 재생성 필요.
SYSTEM_PROMPT_VERSION = "v2"  # (C-62, 2026-05-25) recommendation_score 추가


# v13 라운드 prompt template. {top_n} 만 동적 치환. trace_json / leaf_label 은 user message.
SYSTEM_PROMPT_TEMPLATE = """\
당신은 SKKU InSight 의 토픽 기반 검색 에이전트입니다.

## 역할
- 사용자가 관심 가지는 CS/AI 토픽에 대해 web 검색 도구를 사용해 최신 자료를 모은다.
- 학술 논문 (arXiv, OpenAlex, S2, DBLP), 빅테크 공식 블로그, 신뢰도 높은 테크 뉴스 모두 후보.
- 입력 user trace JSON 의 path / 선택 cluster 를 의도 파악에 활용.
- leaf_label 이 가장 강한 검색 신호 — 이걸 중심으로 query 를 스스로 구성한다.

## 출력 규칙
- 정확히 {top_n} 개 이내 결과를 반환한다.
- 응답은 JSON object 1건: `{{ "results": [...] }}`.
- 각 result 의 필드:
  - title: 원문 제목 (그대로)
  - url: 정식 링크 (가능하면 publisher 원본)
  - abstract_summary: §자가 요약 (아래 §4 참조)
  - publisher_domain: 도메인 (예: arxiv.org, openai.com)
  - publisher_label: 사람 친화 이름 (예: arXiv, OpenAI)
  - published_at: ISO8601 (없으면 null)
  - doi: 학술 자료일 때만 (없으면 null)
  - canonical_url: utm/fbclid/gclid 제거된 URL (가능 시)
  - confidence: 0.0 ~ 1.0 (자료가 leaf 토픽과 얼마나 일치하는지 — topical fit, user 무관)
  - recommendation_score: 1 ~ 10 정수 (§5 참조 — pool 내부 상대 추천도, user trace 반영)
  - raw: 추가 메타 (trust_hint 등)

## §1 검색 query 구성
- LLM 자율 결정 — leaf_label + (선택적으로) trace 의 가장 가까운 cluster 키워드 결합.
- 최신성 우선: 가능하면 최근 90일 기준 정렬.

## §2 중복 제거 hint
- 동일 URL / DOI 가 보이면 1건만 포함.
- 단순 URL 변형 (utm_*, fbclid, gclid) 도 같은 것으로 간주.
- (있다면) 입력의 `seen_urls` 또는 `seen_titles` 리스트와 겹치는 자료 회피 — 사용자가
  이미 받은 추천 카드 중복 방지. 동일 자료의 다른 source (예: arxiv vs 학회 publish) 도 회피.

## §3 신뢰도 기준
- 학술 (arxiv.org/openalex.org/doi.org) — confidence ≥ 0.85
- 빅테크 공식 블로그 — confidence ≥ 0.75
- 테크 뉴스 / 매체 — confidence 0.6 ~ 0.75
- 출처 불명·SEO 스팸 가능성 — 포함하지 않는다.

## §4 자가 요약 (NFR-25)
- 각 검색 결과의 abstract / lede 는 원본 그대로 복사 금지.
- 본인의 말로 1~2문장 (≤200자) 으로 요약하라.
- 한국어로 작성하되 기술 용어는 영어 그대로 둘 수 있다.
- 의역·재구성 — 외부 원문 정확 복제 시 NFR-25 위반.

## §5 recommendation_score (C-62)
- LLM-as-judge 패턴: **수집 풀 안에서의 상대 추천도** 1~10 정수.
- 절대 평가 X — 본 호출에서 반환할 {top_n} 개 자료를 서로 비교해 순위 매김.
- 1 = 풀 안 최하위 추천 (포함은 하지만 사용자 본 trace 와 거리감), 10 = 최상위.
- 입력 user trace JSON 의 path / cluster / leaf_label 과의 personalization fit 반영.
- topical fit (confidence) 와 분리: 같은 confidence 라도 사용자 trace 흐름에 더 잘 맞는
  자료가 더 높은 recommendation_score.
- 동점 가능. 10 자료 모두 동일 점수 부여는 회피 — 최소한 high/mid/low 구분.
"""

# import-time assertion — prompt 가 NFR-25 instruction 을 잃어버리면 즉시 실패.
# audit regression test 가 같은 키워드를 검증 (정적 + 동적 이중 가드).
assert "본인의 말로" in SYSTEM_PROMPT_TEMPLATE, "NFR-25 self-summary instruction missing"
assert "1~2문장" in SYSTEM_PROMPT_TEMPLATE, "NFR-25 length instruction missing"
assert "{top_n}" in SYSTEM_PROMPT_TEMPLATE, "top_n placeholder missing"
assert SYSTEM_PROMPT_VERSION, "SYSTEM_PROMPT_VERSION must be non-empty"
# (C-62, 2026-05-25) recommendation_score instruction guard.
assert "recommendation_score" in SYSTEM_PROMPT_TEMPLATE, "C-62 recommendation_score instruction missing"
assert "1 ~ 10" in SYSTEM_PROMPT_TEMPLATE, "C-62 recommendation_score range missing"


async def search_for_leaf(
    provider: LLMProvider,
    *,
    trace_json: dict[str, Any],
    leaf_label: str,
    parent_cso_topic_id: UUID,
    user_id: UUID,
    top_n: int = 10,
) -> list[SearchResult]:
    """LLM provider 호출. ProviderError / LLMBudgetExceeded 는 orchestrator 가 catch.

    parent_cso_topic_id 는 본 함수 내부에서 직접 사용하지 않지만 호출 사이트 일관성을
    위해 시그니처에 두고, orchestrator 가 검색 결과를 DocumentTopic 으로 변환할 때
    그대로 사용한다.
    """
    return await provider.search_with_tools(
        trace_json,
        leaf_label,
        top_n=top_n,
        user_id=str(user_id),
    )


__all__ = ["SYSTEM_PROMPT_TEMPLATE", "SYSTEM_PROMPT_VERSION", "search_for_leaf"]
