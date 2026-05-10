from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .schemas import ClassifyRequest, ClassifyResponse
from .settings import Settings


# DoRA 어댑터는 아래 system/user 템플릿 전제로 학습됨.
# reference/ax4_clickbait_scorer.py의 build_article_text + build_messages_for_binary와
# 글자 한 자도 다르지 않게 유지할 것. 변경 시 분류 정확도 무효.
_SYSTEM_MSG = (
    "너는 온라인 뉴스 기사의 '낚시성(clickbait) 여부'를 판정하는 이진 분류기다.\n"
    "- 1 = 낚시성이 강한 기사, 0 = 낚시성이 약하거나 거의 없는 기사.\n"
    "- 출력은 반드시 '0' 또는 '1' 한 글자 숫자만 내보낸다."
)


def derive_category(source_name: str, fallback: str) -> str:
    return fallback


def _build_article_text(category: str, title: str, content: str) -> str:
    return (
        f"[카테고리]\n{category}\n\n"
        f"[제목]\n{title}\n\n"
        f"[본문]\n{content}\n"
    )


def _build_user_msg(article_text: str) -> str:
    return (
        "다음 뉴스 기사에 대해 낚시성 여부를 판단하라.\n\n"
        f"{article_text}\n"
        "이 기사가 낚시성이 강하면 1, 그렇지 않으면 0만 출력하라."
    )


def build_prompt(req: ClassifyRequest, tokenizer: Any, settings: Settings) -> str:
    category = derive_category(req.source_name, settings.CATEGORY_FALLBACK)
    article_text = _build_article_text(
        category=category,
        title=req.title.strip(),
        content=req.body.strip(),
    )
    user_msg = _build_user_msg(article_text)
    messages = [
        {"role": "system", "content": _SYSTEM_MSG},
        {"role": "user", "content": user_msg},
    ]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )


def to_classify_response(p1: float, settings: Settings) -> ClassifyResponse:
    decision = "clickbait" if p1 >= settings.CLICKBAIT_THRESHOLD else "clean"
    confidence = max(p1, 1.0 - p1)
    return ClassifyResponse(
        decision=decision,
        confidence=float(confidence),
        model_name=settings.CLICKBAIT_MODEL_NAME,
        adapter_type="dora",
        evaluated_at=datetime.now(timezone.utc),
    )
