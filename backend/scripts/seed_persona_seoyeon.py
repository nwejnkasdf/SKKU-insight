"""seed_persona_seoyeon.py — 발표 demo persona 2 (이서연) 시드 스크립트.

시나리오: 석사 2년차. 1년차 CV/Image Segmentation 연구 → 2년차 NLP (RAG /
long-context) 전환. active_day_counter=220 의 1년 반 사용자.

본 스크립트는 *판만 깐다*:
  - User + UserConsent (personalization)
  - UserCSOTraversal x 2 — archived CV trace (score_tail 0.72) + active NLP trace
  - DynamicLeafTopic x 4 — archived(RPN, SemSeg) + active(RAG, Long-Context LLM)
  - DynamicLeafTopicCSOTopic 매핑
  - UserInterestState — 핵심 CSO·leaf 의 Beta-Bernoulli 상태
  - UserProfile 빈 row (user_profile_generation_job 이 LLM 으로 채울 자리)

시드하지 *않는* 것:
  - Document — 시연 시 collection_job (LLM web_search) 가 생성
  - UserProfile.fusion_candidates — user_profile_generation_job (LLM) 가 생성
  - Recommendation — dashboard 호출 시 engine 이 생성

사용:
  docker compose exec api python -m scripts.seed_persona_seoyeon
  docker compose exec api python -m scripts.seed_persona_seoyeon --force  # 기존 cascade 삭제 후 재시드

이후 시연 절차 (docs/ops/manual-day-control.md):
  python -m scripts.simulate_user_day weekly --user-email seoyeon@demo.skku.ac.kr
    → user_profile_generation_job 이 fusion_candidates 생성
  python -m scripts.simulate_user_day collection --user-email seoyeon@demo.skku.ac.kr
    → LLM web_search 가 RAG/Long-Context 문서 수집
  python -m scripts.simulate_user_day next-day --user-email seoyeon@demo.skku.ac.kr
    → daily lifecycle 평가 + 추천 갱신 대상
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CSOTopic,
    DynamicLeafTopic,
    DynamicLeafTopicCSOTopic,
    User,
    UserConsent,
    UserCSOTraversal,
    UserInterestState,
    UserProfile,
)
from app.db.session import AsyncSessionLocal
from app.security.password import hash_password

# ---- 시드 상수 (발표 본 1회 실행) ----------
EMAIL = "seoyeon@demo.skku.ac.kr"
PASSWORD = "Px2026-Researcher!"  # 정책 통과: 18자, 금칙어 없음, local_part 없음

ACTIVE_DAY = 220                # 현재 active_day_counter (≈1년 반)
ACTIVE_DAY_CV_START = 10        # CV 연구 시작 (석사 1년차 초)
ACTIVE_DAY_CV_LAST = 120        # CV 마지막 활동
ACTIVE_DAY_CV_ARCHIVED = 140    # CV trace archived 된 시점
ACTIVE_DAY_NLP_START = 150      # NLP 전환 (1.5 학기 차)
ACTIVE_DAY_LAST = ACTIVE_DAY - 1  # 어제까지 NLP 활동

logger = logging.getLogger("seed_persona_seoyeon")


# ---------- helpers ----------

async def _lookup_cso(db: AsyncSession, label: str) -> uuid.UUID:
    """CSO 토픽 label → UUID lookup. case-insensitive + underscore/space 양쪽 흡수.

    CSO 3.5 의 label literal 은 보통 Title Case + space ("Computer Vision") 이지만,
    URI 기반 fallback 의 underscore 변형 ("computer_vision") 도 대응.
    미발견 시 SystemExit + 유사 후보 출력.
    """
    target = label.lower().replace("_", " ").strip()
    # 1) 정확 일치 (case + separator 정규화)
    stmt = select(CSOTopic.cso_topic_id).where(
        func.lower(func.replace(CSOTopic.label, "_", " ")) == target
    )
    result = (await db.execute(stmt)).scalar_one_or_none()
    if result is not None:
        return result
    # 2) 미발견 — 유사 후보 5개 ILIKE 로 추출해 진단 메시지
    first_word = target.split(" ", 1)[0] if " " in target else target
    similar_stmt = (
        select(CSOTopic.label)
        .where(CSOTopic.label.ilike(f"%{first_word}%"))
        .limit(5)
    )
    similar = [row[0] for row in (await db.execute(similar_stmt)).all()]
    hint = (
        f" — 유사: {similar}" if similar else " — 유사 후보도 없음 (CSO import 안 됨?)"
    )
    raise SystemExit(
        f"CSO 토픽 '{label}' 미발견{hint}\n"
        f"alembic + import_cso 적용: docker compose exec api python -m scripts.import_cso"
    )


def _now(offset_days: int = 0) -> datetime:
    """현재 시각 + offset (음수 가능)."""
    return datetime.now(UTC) + timedelta(days=offset_days)


def _score(alpha: float, beta: float) -> float:
    """Beta 분포 평균 = alpha / (alpha + beta)."""
    return alpha / (alpha + beta)


def _make_interest_state(
    user_id: uuid.UUID,
    *,
    cso_topic_id: uuid.UUID | None = None,
    leaf_topic_id: uuid.UUID | None = None,
    long_alpha: float,
    long_beta: float,
    short_alpha: float,
    short_beta: float,
    last_event_active_day: int,
) -> UserInterestState:
    """UserInterestState 한 행 — alpha/beta 만 받으면 score 자동 계산."""
    return UserInterestState(
        state_id=uuid.uuid4(),
        user_id=user_id,
        cso_topic_id=cso_topic_id,
        leaf_topic_id=leaf_topic_id,
        long_alpha=long_alpha,
        long_beta=long_beta,
        short_alpha=short_alpha,
        short_beta=short_beta,
        long_score=_score(long_alpha, long_beta),
        short_score=_score(short_alpha, short_beta),
        last_event_active_day=last_event_active_day,
        last_decay_active_day=ACTIVE_DAY,
    )


# ---------- core seed ----------

async def _seed(force: bool) -> None:
    async with AsyncSessionLocal() as db:
        # 1. 기존 user 체크
        existing_id = (
            await db.execute(
                select(User.user_id).where(func.lower(User.email) == EMAIL.lower())
            )
        ).scalar_one_or_none()
        if existing_id is not None:
            if not force:
                raise SystemExit(
                    f"이미 존재하는 user email={EMAIL}. 재시드하려면 --force"
                )
            await db.execute(delete(User).where(User.user_id == existing_id))
            await db.flush()
            logger.info("기존 user cascade 삭제: %s", existing_id)

        # 2. CSO 토픽 UUID lookup
        cso_cv = await _lookup_cso(db, "Computer Vision")
        cso_seg = await _lookup_cso(db, "Image Segmentation")
        cso_ai = await _lookup_cso(db, "Artificial Intelligence")
        cso_nlp = await _lookup_cso(db, "Natural Language Processing")
        cso_ir = await _lookup_cso(db, "Information Retrieval")

        # 3. User + Consent
        user_id = uuid.uuid4()
        user = User(
            user_id=user_id,
            email=EMAIL.lower(),
            password_hash=hash_password(PASSWORD),
            onboarding_complete=True,
            active_day_counter=ACTIVE_DAY,
            last_active_calendar_date=date.today() - timedelta(days=1),
            last_login_at=_now(offset_days=-1),
        )
        db.add(user)
        await db.flush()

        # personalization consent — recommendation 동작 전제
        db.add(
            UserConsent(
                consent_id=uuid.uuid4(),
                user_id=user_id,
                consent_type="personalization",
                agreed_at=_now(offset_days=-200),
                revoked_at=None,
            )
        )

        # 4. UserProfile 빈 row — user_profile_generation_job 이 채울 자리
        db.add(
            UserProfile(
                user_id=user_id,
                recent_signals_summary=None,
                persistent_tendencies_summary=None,
                likely_dislikes_summary=None,
                # fusion_candidates / *_seeds 는 server_default '[]'/'[]' 로 자동
                candidate_pool_ids={},
                generator_version="seed-init",
            )
        )

        # 5. UserCSOTraversal — Archived CV + Active NLP
        archived_trace = UserCSOTraversal(
            trace_id=uuid.uuid4(),
            user_id=user_id,
            path=[cso_cv, cso_seg],
            status="archived",
            origin="behavioral",
            started_active_day=ACTIVE_DAY_CV_START,
            last_activity_active_day=ACTIVE_DAY_CV_LAST,
            archived_at_active_day=ACTIVE_DAY_CV_ARCHIVED,
            score_tail=0.72,  # ≥ 0.6 임계 → Reincarnation 후보
        )
        active_trace = UserCSOTraversal(
            trace_id=uuid.uuid4(),
            user_id=user_id,
            path=[cso_ai, cso_nlp, cso_ir],
            status="active",
            origin="behavioral",
            started_active_day=ACTIVE_DAY_NLP_START,
            last_activity_active_day=ACTIVE_DAY_LAST,
            archived_at_active_day=None,
            score_tail=0.68,
        )
        db.add_all([archived_trace, active_trace])
        await db.flush()

        # 6. DynamicLeafTopic — Archived 2 + Active 2
        leaf_rpn = DynamicLeafTopic(
            leaf_topic_id=uuid.uuid4(),
            user_id=user_id,
            label="Region Proposal Networks",
            label_en="Region Proposal Networks",
            confidence=0.85,
            status="archived",
            created_active_day=40,
            last_signal_active_day=115,
        )
        leaf_semseg = DynamicLeafTopic(
            leaf_topic_id=uuid.uuid4(),
            user_id=user_id,
            label="Semantic Segmentation Methods",
            label_en="Semantic Segmentation Methods",
            confidence=0.78,
            status="archived",
            created_active_day=60,
            last_signal_active_day=110,
        )
        leaf_rag = DynamicLeafTopic(
            leaf_topic_id=uuid.uuid4(),
            user_id=user_id,
            label="Retrieval-Augmented Generation",
            label_en="Retrieval-Augmented Generation",
            confidence=0.88,
            status="active",
            created_active_day=170,
            last_signal_active_day=ACTIVE_DAY_LAST,
        )
        leaf_lc = DynamicLeafTopic(
            leaf_topic_id=uuid.uuid4(),
            user_id=user_id,
            label="Long-Context LLMs",
            label_en="Long-Context LLMs",
            confidence=0.82,
            status="active",
            created_active_day=180,
            last_signal_active_day=ACTIVE_DAY_LAST - 2,
        )
        db.add_all([leaf_rpn, leaf_semseg, leaf_rag, leaf_lc])
        await db.flush()

        # 7. Leaf ↔ CSO 매핑
        db.add_all([
            DynamicLeafTopicCSOTopic(
                leaf_topic_id=leaf_rpn.leaf_topic_id,
                cso_topic_id=cso_seg,
                confidence=0.85,
            ),
            DynamicLeafTopicCSOTopic(
                leaf_topic_id=leaf_semseg.leaf_topic_id,
                cso_topic_id=cso_seg,
                confidence=0.80,
            ),
            DynamicLeafTopicCSOTopic(
                leaf_topic_id=leaf_rag.leaf_topic_id,
                cso_topic_id=cso_ir,
                confidence=0.90,
            ),
            DynamicLeafTopicCSOTopic(
                leaf_topic_id=leaf_lc.leaf_topic_id,
                cso_topic_id=cso_nlp,
                confidence=0.85,
            ),
        ])

        # 8. UserInterestState — admin interest monitor + user_profile LLM 입력용
        # Active 영역 (NLP/IR + leaves): long_score HIGH bucket (≥0.70), short_score HIGH (≥0.60)
        # Archived 영역 (CV/Seg + leaves): long_score 중간 (decay), short_score LOW
        db.add_all([
            # Active CSO chain
            _make_interest_state(
                user_id,
                cso_topic_id=cso_ai,
                long_alpha=10.0, long_beta=4.0,
                short_alpha=6.0, short_beta=3.0,
                last_event_active_day=ACTIVE_DAY_LAST,
            ),
            _make_interest_state(
                user_id,
                cso_topic_id=cso_nlp,
                long_alpha=14.0, long_beta=4.0,
                short_alpha=9.0, short_beta=3.0,
                last_event_active_day=ACTIVE_DAY_LAST,
            ),
            _make_interest_state(
                user_id,
                cso_topic_id=cso_ir,
                long_alpha=12.0, long_beta=4.0,
                short_alpha=8.0, short_beta=2.0,
                last_event_active_day=ACTIVE_DAY_LAST,
            ),
            # Active leaves
            _make_interest_state(
                user_id,
                leaf_topic_id=leaf_rag.leaf_topic_id,
                long_alpha=10.0, long_beta=3.0,
                short_alpha=7.0, short_beta=2.0,
                last_event_active_day=ACTIVE_DAY_LAST,
            ),
            _make_interest_state(
                user_id,
                leaf_topic_id=leaf_lc.leaf_topic_id,
                long_alpha=8.0, long_beta=3.0,
                short_alpha=5.0, short_beta=2.0,
                last_event_active_day=ACTIVE_DAY_LAST - 2,
            ),
            # Archived CV chain — long 은 살아있음, short 은 거의 prior 로 decay
            _make_interest_state(
                user_id,
                cso_topic_id=cso_cv,
                long_alpha=6.0, long_beta=5.0,
                short_alpha=1.5, short_beta=4.0,
                last_event_active_day=ACTIVE_DAY_CV_LAST,
            ),
            _make_interest_state(
                user_id,
                cso_topic_id=cso_seg,
                long_alpha=5.0, long_beta=5.0,
                short_alpha=1.2, short_beta=4.0,
                last_event_active_day=ACTIVE_DAY_CV_LAST,
            ),
            # Archived leaves
            _make_interest_state(
                user_id,
                leaf_topic_id=leaf_rpn.leaf_topic_id,
                long_alpha=4.0, long_beta=4.0,
                short_alpha=1.1, short_beta=4.0,
                last_event_active_day=115,
            ),
            _make_interest_state(
                user_id,
                leaf_topic_id=leaf_semseg.leaf_topic_id,
                long_alpha=3.5, long_beta=4.0,
                short_alpha=1.1, short_beta=4.0,
                last_event_active_day=110,
            ),
        ])

        await db.commit()

        logger.info(
            "시드 완료 — user_id=%s email=%s active_day=%d\n"
            "  archived: CV(score_tail=0.72) → leaves[RPN, SemSeg]\n"
            "  active:   AI→NLP→IR(score_tail=0.68) → leaves[RAG, Long-Context]\n"
            "  다음 단계: simulate_user_day weekly → collection → next-day",
            user_id, EMAIL, ACTIVE_DAY,
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        prog="seed_persona_seoyeon",
        description="발표 demo persona 2 (이서연, 석사 2년차) 시드.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 user 있으면 cascade 삭제 후 재시드",
    )
    args = parser.parse_args(argv)
    asyncio.run(_seed(force=args.force))
    return 0


if __name__ == "__main__":
    sys.exit(main())
