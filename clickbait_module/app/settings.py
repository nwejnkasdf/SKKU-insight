from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    MERGED_MODEL_PATH: str = Field(..., description="DoRA-merged base model dir loaded by vLLM as ordinary base")
    ADAPTER_PATH: Optional[str] = Field(default=None, description="Original DoRA adapter dir (input to merge_adapter.py)")

    DTYPE: Literal["bfloat16", "float16", "float32", "auto"] = "bfloat16"
    MAX_MODEL_LEN: int = 4096
    GPU_MEMORY_UTILIZATION: float = 0.9
    MAX_NUM_SEQS: int = 256
    ENABLE_PREFIX_CACHING: bool = True
    TENSOR_PARALLEL_SIZE: int = 1
    LOGPROBS_TOPK: int = 20

    CLICKBAIT_THRESHOLD: float = 0.5
    CLICKBAIT_MODEL_NAME: str = "ax-4.0-light-dora-clickbait-v1"
    CATEGORY_FALLBACK: str = "ETC"

    STUB_MODE: bool = False
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
