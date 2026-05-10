"""FastAPI 미들웨어 모음 — A2 본문.

등록 순서 (app.main):
1. CORSMiddleware (FastAPI built-in)
2. RequestIdMiddleware — X-Request-Id echo/generate
3. JwtAuthMiddleware — Bearer 검증 + denylist + state 셋팅
4. ConsentGateMiddleware — personalization endpoint 가드

RateLimit 은 slowapi 가 직접 endpoint decorator 로 처리 (별도 미들웨어 X).
ExceptionHandler 는 exception_handler 등록으로 처리.
"""
from __future__ import annotations

__all__: list[str] = []
