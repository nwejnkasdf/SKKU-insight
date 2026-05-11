"""CSO 클러스터 24h Redis 캐시. A3 결정 2.

`GET /topics/cso/clusters` 가 BroadInterest 12 행 + cso_topic JOIN 을 24h 1 회만
DB 조회. Redis cache DB (`REDIS_URL_CACHE`) 의 `RedisKey.cso_clusters_cache()` 키
에 JSON SETEX. CSO 재임포트 종료 시 `scripts/import_cso.py` 가 명시 DEL —
§F-7 cache versioning 잠재 위험은 prefix `v1` 으로 완화 (CSO 스키마 변경 시 v2).
"""
from __future__ import annotations

import json
import logging

from redis.asyncio import Redis

from app.contracts import RedisKey

logger = logging.getLogger(__name__)

# 24 시간 — cso-import.md §clusters TTL. CSO 자체가 안정 12 cluster 라 stale 영향 작음.
CSO_CLUSTERS_CACHE_TTL_SECONDS = 86400


async def get_cluster_cache(redis: Redis) -> list[dict[str, object]] | None:
    """Redis 에서 cached JSON 반환. 없으면 None.

    Returns:
        12 entry list of {cso_topic_id, label, description_ko, document_count}.
    """
    key = RedisKey.cso_clusters_cache()
    raw: str | None = await redis.get(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("cso clusters cache JSON decode 실패: %s — invalidate", e)
        await redis.delete(key)
    return None


async def set_cluster_cache(
    redis: Redis, clusters: list[dict[str, object]]
) -> None:
    """JSON 직렬화 + 24h SETEX. invalidate 는 별도 함수 또는 import_cso.py."""
    key = RedisKey.cso_clusters_cache()
    payload = json.dumps(clusters, ensure_ascii=False)
    await redis.setex(key, CSO_CLUSTERS_CACHE_TTL_SECONDS, payload)


async def invalidate_cluster_cache(redis: Redis) -> None:
    """CSO 재임포트 또는 운영자 수동 invalidate."""
    await redis.delete(RedisKey.cso_clusters_cache())
    logger.info("cso clusters cache invalidate")


__all__ = [
    "CSO_CLUSTERS_CACHE_TTL_SECONDS",
    "get_cluster_cache",
    "invalidate_cluster_cache",
    "set_cluster_cache",
]
