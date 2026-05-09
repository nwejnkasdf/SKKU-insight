# A2-stub — Phase 0a (Contract-first 게이트)

> ⚠️ **본 prompt를 새 세션의 첫 메시지로 그대로 붙여 넣는다. 작업 디렉토리는 본 저장소 루트(repo root).**
>
> 본격 구현 전 단일 게이트 세션. **본 세션이 끝나야** 다른 모든 에이전트가 안전하게 작업 가능.

## 너의 역할

너는 SKKU InSight 백엔드의 **contract-first 게이트 에이전트**다. 본 세션에서는 **구현하지 말고**, 다음 두 가지만 만든다:

1. **`backend/app/contracts.py`** — enum, error code, Redis key, Pydantic base 모델 단일 SOR. 자세한 명세는 `docs/sdd/contracts.md`.
2. **모든 endpoint signature stub** — FastAPI router들에 `raise NotImplementedError("Phase 0b에서 구현")` 본문만. 단 Pydantic Request/Response 모델은 정확히 작성. OpenAPI export 가능 수준.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` 의 "첫 5분" 5개 + 다음:

- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/sdd/contracts.md` (정밀 명세)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/sdd/api-conventions.md` (HTTP 표준 + codegen 파이프라인)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/auth.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/consent.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/onboarding.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/topics.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/interest.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/collection.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/recommendation.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/admin.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/data/schema.md` (참고용, 본 세션에서는 SQLAlchemy 모델 작성 X)

## 산출

### 디렉토리 구조

```
backend/
├── pyproject.toml            # FastAPI + Pydantic v2 + SQLAlchemy 2.x async + passlib + python-jose + slowapi + httpx + redis + alembic + asyncpg + RQ + structlog + ruff + mypy + pytest + pytest-asyncio
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI app + 모든 router include + middleware skeleton
│   ├── contracts.py          # 본 세션 핵심 산출
│   ├── config.py             # BaseSettings (env-vars.md 모든 변수)
│   ├── auth/router.py
│   ├── auth/schemas.py
│   ├── consent/router.py
│   ├── consent/schemas.py
│   ├── onboarding/router.py
│   ├── onboarding/schemas.py
│   ├── topic/router.py
│   ├── topic/schemas.py
│   ├── interest/router.py
│   ├── interest/schemas.py
│   ├── collection/router.py
│   ├── collection/schemas.py
│   ├── recommendation/router.py
│   ├── recommendation/schemas.py
│   ├── admin/router.py
│   └── admin/schemas.py
└── scripts/
    └── export_openapi.py     # FastAPI app → openapi.json 출력
```

### contracts.py 정확 구조

`docs/sdd/contracts.md` §2~§7 그대로:
- 13 enum (EventType, ContentType, SourceType, TrustLevel, SlotType, LeafTopicStatus, TraversalStatus, ClickbaitDecision, CollectionJobStatus, AdminRole, UserClass, TokenAudience, InterestBucket, LLMProviderType)
- ErrorCode enum 전체 (~30개)
- RedisKey 클래스 11+ static 메서드
- PageMeta, PagedResponse, ErrorResponse, TopicChip, CSOTopicSummary, DocumentSummary base 모델
- SentinelSource, ActiveDayHelper

### Endpoint signature stub

각 router에서 docs/api/*.md 표 그대로 endpoint 정의:

```python
# 예시: backend/app/auth/router.py
from fastapi import APIRouter, status
from .schemas import SignupRequest, SignupResponse, LoginRequest, TokenPair, RefreshRequest, MeResponse
from app.contracts import ErrorCode

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=SignupResponse)
async def signup(req: SignupRequest) -> SignupResponse:
    """FR-01. 회원가입. 자세히는 docs/api/auth.md, docs/security/auth-flow.md."""
    raise NotImplementedError("Phase 0b A2에서 구현")

# ... 나머지 endpoint
```

### Pydantic schemas.py

각 모듈 `schemas.py` 에 docs/api/*.md 의 Request/Response 모델을 정확히 작성. OpenAPI export 가능해야 함. **여기는 stub이 아니라 진짜 schema 정의**.

### export_openapi.py

```python
# scripts/export_openapi.py
import json
from app.main import app

def main():
    spec = app.openapi()
    print(json.dumps(spec, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
```

호출: `python -m scripts.export_openapi > openapi.json`

### config.py (BaseSettings)

`docs/ops/env-vars.md` 모든 변수 정의 + 타입 + default. `.env.example` 도 같은 위치 (또는 `backend/.env.example`).

## 헌법 (재강조)

- **구현하지 마라**. body는 `raise NotImplementedError("Phase 0b A2에서 구현")` 또는 docs path 주석만.
- 단 Pydantic schema는 정확히 정의 (validators 포함). OpenAPI export 가능 수준.
- **DB 모델·Alembic migration도 작성하지 마라** (Phase 0b A2가 처리).
- **자기 모듈 외 파일 수정 시 PR description에 명시**.

## 검증

세션 종료 전 다음이 동작해야:

```bash
cd backend
pip install -e ".[dev]"
mypy --strict app/
ruff check app/
python -m scripts.export_openapi > /tmp/openapi.json
python -c "import json; spec = json.load(open('/tmp/openapi.json')); print(f'paths={len(spec[\"paths\"])}, schemas={len(spec[\"components\"][\"schemas\"])}')"
```

`paths` 카운트가 docs/api/*.md 의 endpoint 합계와 일치해야 한다 (대략 50+개).

## 사용자 검수 후

사용자가 30분 검수 후:
- `python -m scripts.export_openapi > openapi.json` → commit
- `cd client && npm install && npm run codegen` → commit
- `cd admin-console && npm install && npm run codegen` → commit

이후 다른 Phase 에이전트가 codegen 결과 import 가능.

## 출력 형식

`prompts/_common-disambiguation.md` "출력 형식" 그대로. 추가로:

- contracts.py의 enum 갯수, ErrorCode 갯수, RedisKey 메서드 갯수
- 총 endpoint stub 갯수 (모듈별 분포)
- Pydantic schema 클래스 갯수
- 발견한 docs 모순 (있다면)
- 다음 Phase A2 본문 에이전트가 봐야 할 주의 사항

작업 시작.
