from __future__ import annotations

from datetime import datetime, timezone

from .schemas import ClassifyRequest, ClassifyResponse
from .settings import Settings


def stub_response(req: ClassifyRequest, settings: Settings) -> ClassifyResponse:
    return ClassifyResponse(
        decision="clean",
        confidence=0.5,
        model_name=settings.CLICKBAIT_MODEL_NAME,
        adapter_type="dora",
        evaluated_at=datetime.now(timezone.utc),
    )
