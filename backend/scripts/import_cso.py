"""CSO 3.4.1 임포트 CLI. A3 (CSO Topic Engine).

backend 컨테이너 안에서 실행. WORKDIR=/app (= backend/) 이므로 `python -m scripts.import_cso`
패턴 통일. 캐시 디렉토리는 컨테이너 내부의 /app/.cache/cso/ — docker-compose 가
호스트 `.cache/` 를 마운트하지 않더라도 컨테이너 lifecycle 내 캐시는 유지.

사용:
    make import-cso                                      # 기본 — 다운로드 (캐시 사용) + parse + INSERT
    docker compose exec api python -m scripts.import_cso --refresh    # 캐시 무시 재다운로드
    docker compose exec api python -m scripts.import_cso --reset      # TRUNCATE 후 재구성
    docker compose exec api python -m scripts.import_cso --reset --refresh
    docker compose exec api python -m scripts.import_cso --dry-run    # parse + cluster 라벨링까지

cso-import.md §전체 워크플로 + decision-backlog P1-5 정합.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.db.engine import get_engine
from app.db.session import AsyncSessionLocal
from app.topic.cso_importer import (
    download_cso,
    insert_cso,
    parse_cso_csv,
    reset_cso_tables,
    seed_broad_interests,
)
from app.topic.mapping import (
    EXPECTED_CLUSTERS,
    assign_cluster_labels,
    missing_seeds,
    verify_cluster_coverage,
)

logger = logging.getLogger("import_cso")

# 캐시 디렉토리 — 컨테이너의 /app/.cache/cso/ (= WORKDIR 기준). 호스트 마운트 없으면
# 컨테이너 lifecycle 내에서만 유지 → docker compose down 후 재다운로드.
CACHE_DIR = Path("/app/.cache/cso") if Path("/app").exists() else Path(".cache/cso")

# BroadInterest 시드 toml — backend/app/config/broad_interests.toml (= /app/app/config/.. in container)
BROAD_INTERESTS_TOML = Path(__file__).resolve().parent.parent / "app" / "config" / "broad_interests.toml"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CSO 3.4.1 importer")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="DELETE cso_topic/cso_topic_parent/broad_interest 후 재구성",
    )
    parser.add_argument(
        "--force-orphan-cso-refs",
        action="store_true",
        dest="force_orphan_cso_refs",
        help=(
            "--reset 가드 (P2-16 + P2-10) 우회. dynamic_leaf_topic / user_cso_traversal "
            "행이 존재할 때만 의미 — leaf 응답의 cso_topic_ids 가 [] 되고 traversal.path "
            "UUID 가 stale 되는 것을 운영자가 명시 허용. 미설정 시 RuntimeError."
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=".cache/cso/ 무시하고 재다운로드",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse + cluster 라벨링까지. INSERT 안 함. 검증용.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    # 1. 다운로드
    csv_path = download_cso(
        settings.CSO_DOWNLOAD_URL, CACHE_DIR, refresh=args.refresh
    )
    logger.info("CSV: %s (%.1f MB)", csv_path, csv_path.stat().st_size / 1e6)

    # 2. parse
    topics = parse_cso_csv(csv_path)
    if not topics:
        logger.error("parse 결과 0건 — CSV 형식 확인")
        return 1

    # 3. 누락 seed 검증 (§F-6 가드)
    missing = missing_seeds(topics)
    if missing:
        logger.error("12 cluster seed 매칭 실패: %s", missing)
        return 2

    # 4. BFS 라벨링
    cluster_assignments = assign_cluster_labels(topics)
    miss_cluster = verify_cluster_coverage(cluster_assignments)
    if miss_cluster:
        logger.error("cluster 매핑 누락: %s", sorted(miss_cluster))
        return 3
    logger.info(
        "cluster 매핑 OK: %d nodes / %d clusters",
        len(cluster_assignments),
        len(EXPECTED_CLUSTERS),
    )

    if args.dry_run:
        logger.info("--dry-run: INSERT skip")
        return 0

    # 5. DB INSERT — 단일 transaction 으로 reset + insert + seed 묶음 (Codex 감사 Critical fix).
    # 이전 코드: reset 후 즉시 commit + insert/seed 별도 commit → seed_broad_interests 가
    # B-8 RuntimeError 던지면 reset 만 완료, insert/seed 는 rollback → DB empty 상태 잔존.
    # 수정 후: 전체를 session.begin() 으로 묶어 RuntimeError 시 reset 까지 rollback.
    engine = get_engine()
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                if args.reset:
                    await reset_cso_tables(
                        session, force_orphan=args.force_orphan_cso_refs
                    )
                uri_to_id = await insert_cso(session, topics, cluster_assignments)
                inserted = await seed_broad_interests(
                    session, BROAD_INTERESTS_TOML, uri_to_id, topics
                )
            # session.begin() context manager 종료 시 자동 commit (예외 시 rollback)
            logger.info(
                "INSERT 완료: cso_topic=%d / broad_interest=%d",
                len(uri_to_id),
                inserted,
            )
    finally:
        await engine.dispose()

    # 6. Redis 캐시 invalidate (§F-7)
    try:
        from redis.asyncio import Redis

        from app.contracts import RedisKey

        client = Redis.from_url(settings.REDIS_URL_CACHE, decode_responses=True)
        try:
            await client.delete(RedisKey.cso_clusters_cache())
            logger.info("Redis cache invalidate: %s", RedisKey.cso_clusters_cache())
        finally:
            await client.aclose()
    except Exception as e:
        logger.warning("Redis invalidate 실패 (무시 가능 — 24h TTL 후 자연 만료): %s", e)

    return 0


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
