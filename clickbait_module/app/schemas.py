from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ClassifyMeta(BaseModel):
    published_at: Optional[datetime] = None
    url: Optional[str] = None
    summary: Optional[str] = None


class ClassifyRequest(BaseModel):
    document_id: UUID
    title: str
    body: str = Field(..., max_length=8000)
    source_name: str
    source_type: Literal["academic", "vendor_blog", "tech_news"]
    language: Literal["ko", "en"]
    meta: ClassifyMeta = Field(default_factory=ClassifyMeta)


class ClassifyResponse(BaseModel):
    decision: Literal["clickbait", "clean"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    model_name: str
    adapter_type: Literal["dora"] = "dora"
    evaluated_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model_loaded: bool
    stub_mode: bool
    error: Optional[str] = None
