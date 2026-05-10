from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status

from .schemas import ClassifyRequest, ClassifyResponse, HealthResponse
from .settings import Settings, get_settings
from .shim import build_prompt, to_classify_response
from .stub import stub_response


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.LOG_LEVEL)
    logger.info(
        "boot: cpu_count=%s stub_mode=%s merged_model_path=%s",
        os.cpu_count(),
        settings.STUB_MODE,
        settings.MERGED_MODEL_PATH,
    )
    app.state.engine = None
    app.state.tokenizer = None
    app.state.model_loaded = False
    app.state.load_error = None
    if not settings.STUB_MODE:
        try:
            from .inference import ClickbaitEngine

            engine = await ClickbaitEngine.create(settings)
            app.state.engine = engine
            app.state.tokenizer = engine.tokenizer
            app.state.model_loaded = True
            logger.info("vllm engine loaded; entering serving mode")
        except Exception as exc:  # noqa: BLE001 — broad on purpose: any vllm failure → stub
            logger.exception("vllm engine load failed; falling back to stub mode")
            app.state.load_error = str(exc)
    yield


app = FastAPI(lifespan=lifespan, title="clickbait-detector")


@app.get("/health", response_model=HealthResponse)
async def health(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    loaded: bool = bool(request.app.state.model_loaded)
    error: Optional[str] = request.app.state.load_error
    serving = loaded or settings.STUB_MODE
    return HealthResponse(
        status="ok" if serving else "degraded",
        model_loaded=loaded,
        stub_mode=settings.STUB_MODE,
        error=error,
    )


@app.post("/classify", response_model=ClassifyResponse)
async def classify(
    req: ClassifyRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ClassifyResponse:
    engine = request.app.state.engine
    tokenizer = request.app.state.tokenizer
    if engine is None or tokenizer is None:
        return stub_response(req, settings)
    try:
        prompt = build_prompt(req, tokenizer, settings)
        _, p1 = await engine.classify(prompt)
    except RuntimeError as exc:
        logger.exception("classify failed for document_id=%s", req.document_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return to_classify_response(p1, settings)
