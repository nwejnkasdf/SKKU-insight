"""Topic 모듈 startup·shutdown hook. A3 결정 6.

`backend/app/lifespan.py` 의 main lifespan 이 본 모듈의 `topic_startup` / `topic_shutdown`
을 호출. NetworkX CSO 그래프 빌드 → `app.state.cso_graph` 등록.

부트 시간: DB → 메모리 빌드 (cso_topic ~14k + cso_topic_parent ~14k) 평소 5초 이내.
30 초 초과 시 §F-3 후속 (decision-backlog P1 후속 — lazy / readiness probe 분리).
"""
from __future__ import annotations

import logging
import time

import structlog
from fastapi import FastAPI

from app.db.engine import get_engine
from app.topic.graph import build_cso_graph, verify_cso_import

logger = logging.getLogger(__name__)


async def topic_startup(app: FastAPI) -> None:
    """DB → NetworkX 그래프 빌드 → verify → app.state.cso_graph 등록.

    cso_topic 이 비어 있으면 (CSO 미임포트) 빈 그래프 + cluster 검증 실패로 RuntimeError.
    개발자는 `make import-cso` 먼저 실행해야 함.

    cluster 매핑 누락 (12 seed 매칭 실패) 만 fatal. cycle 은 WARN + 계속 (§F-5).
    """
    structlog_logger = structlog.get_logger("topic_startup")
    started = time.monotonic()
    engine = get_engine("api")
    g = await build_cso_graph(engine)
    elapsed_build = time.monotonic() - started
    if g.number_of_nodes() == 0:
        structlog_logger.warning(
            "cso_graph_empty",
            elapsed_seconds=round(elapsed_build, 2),
            hint="run `make import-cso` first",
        )
        # 빈 그래프도 등록 (test 환경 호환). verify 는 skip.
        app.state.cso_graph = g
        return
    verify_cso_import(g)
    app.state.cso_graph = g
    structlog_logger.info(
        "cso_graph_ready",
        nodes=g.number_of_nodes(),
        edges=g.number_of_edges(),
        elapsed_seconds=round(elapsed_build, 2),
    )


async def topic_shutdown(app: FastAPI) -> None:
    """Cleanup. NetworkX 그래프는 Python GC 가 회수 — 명시 작업 없음."""
    # 향후 그래프 캐시를 외부 (Redis) 에 두면 본 hook 에서 명시 close.
    if hasattr(app.state, "cso_graph"):
        delattr(app.state, "cso_graph")


__all__ = ["topic_shutdown", "topic_startup"]
