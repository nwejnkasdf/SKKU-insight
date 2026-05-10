# ax4_clickbait_scorer.py
# A.X-4.0 Light 기반 단일 기사 낚시성 점수 산정 모듈 (IG 제거 버전)
#
# - 모델/토크나이저는 전역에서 한 번만 로드 (lazy init)
# - 점수 산정 로직은 기존 analyze_article_with_attribution_IG의
#   "p0, p1, score_logit_diff 계산 부분"을 그대로 가져와서 사용
# - IG(토큰 기여도) 계산은 완전히 제거

import os
import json
from typing import Dict, Any, Optional, Tuple

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

# =========================
# 전역 상태 (모델 1회 로딩)
# =========================

_MODEL = None
_TOKENIZER = None
_DEVICE = None
_DTYPE = None

# 필요하면 여기 기본 경로만 네 환경에 맞게 수정해서 쓰면 됨
DEFAULT_MODEL_PATH = "/content/A.X-4.0-Light"


def init_model(
    model_path: str = DEFAULT_MODEL_PATH,
    device: Optional[str] = None,
    dtype: Optional[torch.dtype] = None,
) -> Tuple[AutoModelForCausalLM, AutoTokenizer, str, torch.dtype]:
    """
    A.X-4.0 Light 모델과 토크나이저를 전역에 1회 로드.

    이미 한 번 로드했다면, 다시 로드하지 않고 기존 객체를 그대로 반환한다.
    """
    global _MODEL, _TOKENIZER, _DEVICE, _DTYPE

    if _MODEL is not None and _TOKENIZER is not None:
        return _MODEL, _TOKENIZER, _DEVICE, _DTYPE

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if dtype is None:
        # 원본 노트북과 동일하게: GPU면 bfloat16, 아니면 float32
        dtype = torch.bfloat16 if device == "cuda" else torch.float32

    print(f"[ax4_clickbait_scorer] Loading model from: {model_path}")
    print(f"[ax4_clickbait_scorer] device={device}, dtype={dtype}")

    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        trust_remote_code=True,
    )
    if tokenizer.pad_token is None and tokenizer.eos_token is not None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=dtype,
        device_map="auto" if device == "cuda" else None,
        trust_remote_code=True,
    )
    model.eval()

    _MODEL = model
    _TOKENIZER = tokenizer
    _DEVICE = device
    _DTYPE = dtype

    print("[ax4_clickbait_scorer] Model loaded.")
    return _MODEL, _TOKENIZER, _DEVICE, _DTYPE


# =========================
# 프롬프트 / 토큰 구성
# =========================

def build_article_text(article: Dict[str, Any]) -> str:
    """
    기사 JSON(dict)을 사람이 읽기 좋은 텍스트로 변환.

    기대하는 키:
      - newsCategory
      - newsTitle
      - newsContent
    """
    category = (article.get("newsCategory") or "").strip()
    title = (article.get("newsTitle") or "").strip()
    content = (article.get("newsContent") or "").strip()

    text = f"""[카테고리]
{category}

[제목]
{title}

[본문]
{content}
"""
    return text


def build_messages_for_binary(article: Dict[str, Any]) -> list:
    """
    낚시성(0/1) 분류용 chat 메시지 구성.

    ★ 중요: 이 프롬프트는 기존 노트북의 것과 동일하게 유지해야
    p0/p1/score_logit_diff 값이 완전히 같게 나온다.
    """
    system_msg = (
        "너는 온라인 뉴스 기사의 '낚시성(clickbait) 여부'를 판정하는 이진 분류기다.\n"
        "- 1 = 낚시성이 강한 기사, 0 = 낚시성이 약하거나 거의 없는 기사.\n"
        "- 출력은 반드시 '0' 또는 '1' 한 글자 숫자만 내보낸다."
    )

    article_text = build_article_text(article)
    user_msg = (
        "다음 뉴스 기사에 대해 낚시성 여부를 판단하라.\n\n"
        f"{article_text}\n"
        "이 기사가 낚시성이 강하면 1, 그렇지 않으면 0만 출력하라."
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]
    return messages


def _build_prefix_ids_for_binary(article: Dict[str, Any]) -> torch.Tensor:
    """
    chat 템플릿을 적용한 prefix 토큰 시퀀스 생성.

    이 prefix 바로 다음에 나올 토큰을 0/1 중 하나로 해석한다.
    (원본 노트북에서와 동일한 방식)
    """
    assert _TOKENIZER is not None and _DEVICE is not None, \
        "init_model()이 먼저 호출되어야 합니다."

    messages = build_messages_for_binary(article)
    prefix_ids = _TOKENIZER.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
    ).to(_DEVICE)  # [1, L]
    return prefix_ids


# =========================
# 점수 산정 (IG 제거 버전)
# =========================

def _compute_logits_for_prefix(prefix_ids: torch.Tensor) -> torch.Tensor:
    """
    주어진 prefix_ids에 대해 마지막 위치의 logits 벡터를 반환.
    (원본 analyze_article_with_attribution_IG의 p0/p1 계산 부분을 그대로 분리)
    """
    assert _MODEL is not None and _TOKENIZER is not None, \
        "init_model()이 먼저 호출되어야 합니다."

    # attention mask
    if _TOKENIZER.pad_token_id is not None:
        attention_mask = (prefix_ids != _TOKENIZER.pad_token_id).long()
    else:
        attention_mask = torch.ones_like(prefix_ids)

    # 0/1 토큰 id
    id0 = _TOKENIZER("0", add_special_tokens=False).input_ids[0]
    id1 = _TOKENIZER("1", add_special_tokens=False).input_ids[0]

    emb_layer = _MODEL.get_input_embeddings()
    with torch.no_grad():
        real_embeds = emb_layer(prefix_ids)   # [1, L, d]

    # 원본 입력(alpha=1.0)으로 p0, p1, score 계산
    inputs_embeds_for_score = real_embeds.detach().clone()
    outputs = _MODEL(
        inputs_embeds=inputs_embeds_for_score,
        attention_mask=attention_mask,
    )
    last_logits = outputs.logits[0, -1, :]  # [V]

    return last_logits, id0, id1


def score_article(article: Dict[str, Any]) -> Dict[str, Any]:
    """
    단일 기사에 대한 낚시성 점수 및 확률을 계산한다.

    반환값은 원본 IG 기반 함수(analyze_article_with_attribution_IG)에서
    p0/p1/score_logit_diff를 계산하던 로직과 완전히 동일한 결과를 낸다.

    Returns:
        {
          "p0": float,                  # 다음 토큰이 "0"일 확률
          "p1": float,                  # 다음 토큰이 "1"일 확률 (낚시성 확률)
          "score_logit_diff": float,    # logit(1) - logit(0)
          "p_clickbait": float,         # p1 그대로 (편의상 별도 필드)
          "pred_label_clickbait": int,  # 0 또는 1
          "conf_llm": float,            # max(p0, p1)
        }
    """
    if _MODEL is None or _TOKENIZER is None:
        # lazy init: 호출 시점에 한 번만 로드
        init_model()

    # 1) prefix ids 생성 (chat 템플릿 적용)
    prefix_ids = _build_prefix_ids_for_binary(article)  # [1, L]

    # 2) 마지막 토큰 logits 계산 (원본 코드 그대로)
    last_logits, id0, id1 = _compute_logits_for_prefix(prefix_ids)

    # 3) 2-클래스 softmax (0 vs 1)
    two = torch.stack([last_logits[id0], last_logits[id1]], dim=0)  # [2]
    probs = torch.softmax(two, dim=0)
    p0 = probs[0].item()
    p1 = probs[1].item()

    # 4) score = logit(1) - logit(0)
    score_logit_diff = (last_logits[id1] - last_logits[id0]).item()

    # 5) 라벨 및 confidence
    pred_label = 1 if p1 >= p0 else 0
    conf = max(p0, p1)

    return {
        "p0": p0,
        "p1": p1,
        "score_logit_diff": score_logit_diff,
        "p_clickbait": p1,
        "pred_label_clickbait": pred_label,
        "conf_llm": conf,
    }


# =========================
# 편의 함수 (파일에서 바로 읽기)
# =========================

def score_article_from_path(path: str) -> Dict[str, Any]:
    """
    기사 JSON 파일 경로를 받아서 바로 점수를 계산하는 헬퍼.

    실험 코드에서 빠르게 쓰고 싶을 때:
        from ax4_clickbait_scorer import score_article_from_path
        result = score_article_from_path("/path/to/article.json")
    """
    with open(path, "r", encoding="utf-8") as f:
        article = json.load(f)
    return score_article(article)


# 모듈 import 시 자동 로딩을 원하면, 아래 주석을 풀어서 쓸 수도 있다.
# 다만 Colab/서버에서 경로를 매번 바꿔야 한다면, 명시적으로 init_model() 호출하는 걸 추천.
#
# init_model()