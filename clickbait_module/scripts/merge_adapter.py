"""DoRA 어댑터를 base 모델에 머지하여 일반 base model 가중치로 변환.

vLLM은 결과 디렉토리를 일반 HF 모델처럼 로드한다 (LoRARequest 미사용).

사용:
    python -m clickbait_module.scripts.merge_adapter \\
        --base skt/A.X-4.0-Light \\
        --adapter clickbait_module/adapter \\
        --output ./merged-clickbait
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


_DTYPE_MAP = {
    "bfloat16": torch.bfloat16,
    "float16": torch.float16,
    "float32": torch.float32,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="base 모델 HF id 또는 로컬 경로")
    parser.add_argument("--adapter", required=True, help="DoRA 어댑터 디렉토리")
    parser.add_argument("--output", required=True, help="머지 결과 저장 디렉토리")
    parser.add_argument("--dtype", default="bfloat16", choices=list(_DTYPE_MAP.keys()))
    args = parser.parse_args()

    torch_dtype = _DTYPE_MAP[args.dtype]
    adapter_dir = Path(args.adapter)
    out_dir = Path(args.output)

    print(f"loading base from {args.base} (dtype={args.dtype})")
    base = AutoModelForCausalLM.from_pretrained(
        args.base,
        torch_dtype=torch_dtype,
        trust_remote_code=True,
        device_map="auto",
    )

    print(f"attaching adapter from {adapter_dir}")
    model = PeftModel.from_pretrained(base, str(adapter_dir), torch_dtype=torch_dtype)

    print("merging DoRA scaling into base weights")
    merged = model.merge_and_unload()

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"saving merged model to {out_dir}")
    merged.save_pretrained(out_dir, safe_serialization=True)

    print("saving tokenizer (from adapter dir, learning-time tokenizer)")
    tokenizer = AutoTokenizer.from_pretrained(str(adapter_dir), trust_remote_code=True)
    tokenizer.save_pretrained(out_dir)

    _copy_if_exists(adapter_dir / "chat_template.jinja", out_dir / "chat_template.jinja")

    run_meta = adapter_dir / "run_meta.json"
    if run_meta.exists():
        with open(run_meta, encoding="utf-8") as f:
            meta = json.load(f)
        with open(out_dir / "run_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"copied run_meta.json (id0={meta.get('id0')}, id1={meta.get('id1')})")
    else:
        print("warning: run_meta.json not found in adapter dir; id0/id1 will fall back to tokenizer encode")

    print("done")


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copy2(src, dst)
        print(f"copied {src.name}")


if __name__ == "__main__":
    main()
