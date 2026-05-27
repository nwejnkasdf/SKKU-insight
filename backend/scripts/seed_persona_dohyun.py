"""seed_persona_dohyun.py — 발표 demo persona 3 (김도현, 학부 4학년) Day 0 시드.

42일 시뮬레이션의 시작점. 가입 직후 상태 (active_day_counter=0).
시나리오: 컴공 4학년 졸업 프로젝트. AI 클러스터 선택으로 onboarding.

본 스크립트는 Day 0 상태만:
  - User + UserConsent (personalization)
  - onboarding_complete=True, active_day_counter=0
  - bootstrap_interest_state 호출 → AI 클러스터 + 1-hop 자식 prefilled
  - onboarding_boost trace 1개 (path=[AI seed cso])

42일 시뮬레이션은 별도 스크립트:
  python -m scripts.simulate_persona_dohyun_42days

사용:
  docker compose exec api python -m scripts.seed_persona_dohyun
  docker compose exec api python -m scripts.seed_persona_dohyun --force
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_engine
from app.db.models import (
    BroadInterest,
    CSOTopic,
    DynamicLeafTopic,
    DynamicLeafTopicCSOTopic,
    User,
    UserConsent,
)
from app.db.session import AsyncSessionLocal
from app.interest.service import bootstrap_interest_state
from app.redis import get_redis
from app.security.password import hash_password
from app.topic.graph import build_cso_graph

# ---- 시드 상수 ----------
EMAIL = "dohyun@demo.skku.ac.kr"
PASSWORD = "Px2026-Senior-CS!"  # 정책: 18자, 금칙어 없음, local_part 미포함
CLUSTER_NAME = "AI"  # BroadInterest.name — 김도현은 AI 클러스터 단일 선택

logger = logging.getLogger("seed_persona_dohyun")


async def _resolve_cluster_id(db: AsyncSession, name: str) -> uuid.UUID:
    """BroadInterest.name → broad_interest_id lookup."""
    stmt = select(BroadInterest.broad_interest_id).where(BroadInterest.name == name)
    result = (await db.execute(stmt)).scalar_one_or_none()
    if result is None:
        names_stmt = select(BroadInterest.name)
        names = [r[0] for r in (await db.execute(names_stmt)).all()]
        raise SystemExit(
            f"BroadInterest name='{name}' 미발견. 사용 가능: {names}"
        )
    return result


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

        # 2. AI 클러스터 ID lookup
        cluster_id = await _resolve_cluster_id(db, CLUSTER_NAME)

        # 3. User + Consent
        user_id = uuid.uuid4()
        user = User(
            user_id=user_id,
            email=EMAIL.lower(),
            password_hash=hash_password(PASSWORD),
            onboarding_complete=True,
            active_day_counter=0,
            last_active_calendar_date=date.today(),
            last_login_at=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()
        db.add(
            UserConsent(
                consent_id=uuid.uuid4(),
                user_id=user_id,
                consent_type="personalization",
                agreed_at=datetime.now(UTC),
                revoked_at=None,
            )
        )
        await db.flush()

        # 4. bootstrap_interest_state — cluster + 1-hop 자식 row 자동 INSERT
        #    + onboarding_boost trace 자동 생성
        graph = await build_cso_graph(get_engine())
        redis = get_redis("default")
        prefilled = await bootstrap_interest_state(
            db,
            graph,
            user=user,
            cluster_ids=[cluster_id],
            active_day=0,
            redis=redis,
        )

        # 5. "Natural Language Processing" active dynamic leaf seed.
        #    Narrative: "AI 입문 수업에서 NLP 토픽을 첫 키워드로 다루면서 시스템이
        #    추적 시작" — 시뮬레이션 결정성 보장 (collection_job 의 1번 분기 = active leaf
        #    가 잡혀 NLP 영역 docs fetch). 본 leaf 없으면 fallback adjacent 가 hash 기반
        #    엉뚱한 영역 (system theory 등) fetch → NLP docs 부재 → 시뮬 실패.
        nlp_cso = (
            await db.execute(
                select(CSOTopic.cso_topic_id).where(
                    func.lower(CSOTopic.label) == "natural language processing"
                )
            )
        ).scalar_one_or_none()
        if nlp_cso is None:
            raise SystemExit(
                "CSO 'natural language processing' 미발견 — import_cso 적용 확인"
            )
        leaf_id = uuid.uuid4()
        db.add(
            DynamicLeafTopic(
                leaf_topic_id=leaf_id,
                user_id=user_id,
                label="Natural Language Processing",
                label_en="Natural Language Processing",
                confidence=0.80,
                status="active",
                created_active_day=0,
                last_signal_active_day=0,
            )
        )
        await db.flush()
        db.add(
            DynamicLeafTopicCSOTopic(
                leaf_topic_id=leaf_id,
                cso_topic_id=nlp_cso,
                confidence=0.80,
            )
        )

        await db.commit()

        logger.info(
            "시드 완료 — user_id=%s email=%s active_day=0\n"
            "  cluster=AI (broad_interest_id=%s)\n"
            "  prefilled interest_state rows=%d (cluster + 1-hop children)\n"
            "  seeded 1 active dynamic leaf: 'Natural Language Processing' "
            "(anchored to NLP CSO=%s)\n"
            "  다음 단계: python -m scripts.simulate_persona_dohyun_42days",
            user_id,
            EMAIL,
            cluster_id,
            prefilled,
            nlp_cso,
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        prog="seed_persona_dohyun",
        description="발표 demo persona 3 (김도현, 학부 4학년) Day 0 시드.",
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
