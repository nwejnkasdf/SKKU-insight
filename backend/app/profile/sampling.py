"""C-53 (2026-05-24) trace softmax sampling — 매일 같은 trace 반복 회피.

기존: archived 는 `get_top_archived_trace` deterministic top-1, active 는 max(score_tail)
→ 매일 같은 trace 부활 / 같은 active 가 fusion seed → "매일 새 발견" 본질과 충돌.

본 모듈: trace 풀에서 score_tail 기반 softmax sampling — temperature (T) 로
deterministic vs random 균형 조정. archived / active 모두 동일 함수 사용 (C-53 followup,
사용자 결정 "active_trace 도 같은 기준의 softmax").

수식: P(trace_i) = exp(score_i / T) / Σ_j exp(score_j / T)

- T → 0 = top score 가 거의 1.0 (= 기존 deterministic 동작)
- T → ∞ = uniform random (모든 trace 동등)
- **T = 0.3** (default 추천): score_tail 0.6~1.0 분포 기준 top 에 약 70~80% weight,
  others 20~30% — 매일 다양성 + top 우선 유지

Settings env `REINCARNATION_SAMPLING_TEMPERATURE` 으로 조정 가능 (archived + active 공통).
"""
from __future__ import annotations

import math
import random
from uuid import UUID

from app.db.models import UserCSOTraversal


def softmax_sample_trace(
    traces: list[UserCSOTraversal],
    *,
    temperature: float = 0.3,
    rng: random.Random | None = None,
) -> UserCSOTraversal | None:
    """trace 풀에서 softmax (score_tail / T) sampling. archived / active 공용.

    Args:
        traces: trace 후보 (archived 는 get_archived_traces_with_score, active 는
            get_active_traces 결과)
        temperature: softmax 의 T — 작을수록 top 집중, 클수록 uniform
        rng: random.Random 인스턴스 (테스트 reproducibility — None 시 module default)

    Returns:
        선택된 trace — 풀이 비었으면 None.

    안정성: T → 0 일 때 overflow 회피 — score 정규화 (max 빼기) 후 exp.
    """
    if not traces:
        return None
    if len(traces) == 1:
        return traces[0]
    rng = rng or random
    # 수치 안정성: max score 빼서 exp overflow 방지
    scores = [t.score_tail for t in traces]
    max_score = max(scores)
    # T 가 너무 작으면 logits 폭증 → clip (T=0.01 같은 극단 회피)
    safe_t = max(temperature, 0.05)
    logits = [(s - max_score) / safe_t for s in scores]
    exp_logits = [math.exp(lg) for lg in logits]
    total = sum(exp_logits)
    if total <= 0:
        # numeric edge — uniform fallback
        return rng.choice(traces)
    probs = [e / total for e in exp_logits]
    # cumulative 으로 sampling
    r = rng.random()
    cum = 0.0
    for trace, p in zip(traces, probs, strict=True):
        cum += p
        if r <= cum:
            return trace
    # rounding edge fallback — 마지막 trace
    return traces[-1]


def sampled_trace_id(
    traces: list[UserCSOTraversal],
    *,
    temperature: float = 0.3,
    rng: random.Random | None = None,
) -> UUID | None:
    """편의 wrapper — sampling 결과의 trace_id 만 반환."""
    selected = softmax_sample_trace(
        traces, temperature=temperature, rng=rng
    )
    return selected.trace_id if selected is not None else None


__all__ = [
    "softmax_sample_trace",
    "sampled_trace_id",
]
