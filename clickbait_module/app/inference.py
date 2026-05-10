from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

from transformers import AutoTokenizer, PreTrainedTokenizerBase
from vllm import AsyncEngineArgs, AsyncLLMEngine, SamplingParams
from vllm.utils import random_uuid

from .settings import Settings


logger = logging.getLogger(__name__)


class ClickbaitEngine:
    def __init__(
        self,
        engine: AsyncLLMEngine,
        tokenizer: PreTrainedTokenizerBase,
        id0: int,
        id1: int,
        settings: Settings,
    ) -> None:
        self._engine = engine
        self.tokenizer = tokenizer
        self._id0 = id0
        self._id1 = id1
        self._settings = settings

    @classmethod
    async def create(cls, settings: Settings) -> ClickbaitEngine:
        args = AsyncEngineArgs(
            model=settings.MERGED_MODEL_PATH,
            tokenizer=settings.MERGED_MODEL_PATH,
            dtype=settings.DTYPE,
            max_model_len=settings.MAX_MODEL_LEN,
            gpu_memory_utilization=settings.GPU_MEMORY_UTILIZATION,
            max_num_seqs=settings.MAX_NUM_SEQS,
            enable_prefix_caching=settings.ENABLE_PREFIX_CACHING,
            tensor_parallel_size=settings.TENSOR_PARALLEL_SIZE,
            trust_remote_code=True,
        )
        engine = AsyncLLMEngine.from_engine_args(args)
        tokenizer = AutoTokenizer.from_pretrained(
            settings.MERGED_MODEL_PATH,
            trust_remote_code=True,
        )
        if tokenizer.pad_token is None and tokenizer.eos_token is not None:
            tokenizer.pad_token = tokenizer.eos_token
        id0, id1 = _resolve_token_ids(settings.MERGED_MODEL_PATH, tokenizer)
        logger.info(
            "vllm engine ready: max_num_seqs=%s gpu_util=%s id0=%s id1=%s",
            settings.MAX_NUM_SEQS,
            settings.GPU_MEMORY_UTILIZATION,
            id0,
            id1,
        )
        return cls(engine, tokenizer, id0, id1, settings)

    async def classify(self, prompt: str) -> tuple[float, float]:
        params = SamplingParams(
            max_tokens=1,
            logprobs=self._settings.LOGPROBS_TOPK,
            temperature=0.0,
        )
        request_id = random_uuid()
        result_gen = self._engine.generate(prompt, params, request_id)
        final = None
        async for out in result_gen:
            final = out
        if final is None or not final.outputs:
            raise RuntimeError("vllm produced no output")
        completion = final.outputs[0]
        if not completion.logprobs:
            raise RuntimeError("vllm response has no logprobs")
        logprob_map = completion.logprobs[0]
        l0 = self._extract_logprob(logprob_map, self._id0)
        l1 = self._extract_logprob(logprob_map, self._id1)
        if l0 == float("-inf") and l1 == float("-inf"):
            raise RuntimeError(
                f"id0={self._id0} and id1={self._id1} not in top-{self._settings.LOGPROBS_TOPK}"
            )
        return _two_class_softmax(l0, l1)

    @staticmethod
    def _extract_logprob(logprob_map: Any, token_id: int) -> float:
        if token_id in logprob_map:
            entry = logprob_map[token_id]
            return float(entry.logprob if hasattr(entry, "logprob") else entry)
        return float("-inf")


def _resolve_token_ids(merged_model_path: str, tokenizer: PreTrainedTokenizerBase) -> tuple[int, int]:
    meta_path = os.path.join(merged_model_path, "run_meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        return int(meta["id0"]), int(meta["id1"])
    id0 = tokenizer("0", add_special_tokens=False).input_ids[0]
    id1 = tokenizer("1", add_special_tokens=False).input_ids[0]
    logger.warning(
        "run_meta.json not found at %s; falling back to tokenizer (id0=%s id1=%s)",
        meta_path,
        id0,
        id1,
    )
    return int(id0), int(id1)


def _two_class_softmax(l0: float, l1: float) -> tuple[float, float]:
    m = max(l0, l1)
    e0 = math.exp(l0 - m) if l0 != float("-inf") else 0.0
    e1 = math.exp(l1 - m) if l1 != float("-inf") else 0.0
    denom = e0 + e1
    if denom == 0.0:
        raise RuntimeError("two-class softmax denominator is zero")
    return e0 / denom, e1 / denom
