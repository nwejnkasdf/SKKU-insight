# A5 — Clickbait Detector (Phase 1)

> 작업 디렉토리: `/Users/hyojung/학교 과제/소프트웨어공학개론/`
> **사전조건**: A2 backend + A4 collection 완료. **P0-1 (사용자가 DoRA 모듈 경로 공유) 해결되어야 본 세션 시작 가능**. 미해결 시 stub 응답으로 진행 후 모듈 공유 받으면 wrap만 갱신.

## 너의 역할

사용자가 보유한 DoRA 파인튜닝된 `A.x 4.0 light` 낚시성 탐지 모듈을 별도 컨테이너로 wrapping. 백엔드는 httpx로 호출.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/algorithms/clickbait-integration.md` (전체)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/data/schema.md` (ClickbaitResult 부분)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/sdd/architecture.md` (Clickbait DoRA 컴포넌트)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/decision-backlog.md` (P0-1 항목)

## 산출

### 1. `services/clickbait-detector/` 컨테이너
- `Dockerfile` — Python 3.10+ + transformers + base 모델 + DoRA 어댑터 weights
- `app/main.py` — FastAPI `/classify` (POST), `/health` (GET)
- 입출력 스키마는 `algorithms/clickbait-integration.md` "인터페이스 계약" 그대로
- 모델 메타: `model_name="ax-4.0-light-dora-clickbait-v1"`, `adapter_type="dora"`

### 2. backend `app/clickbait_client/`
- `ClickbaitClassifier` Protocol 구현체 `AxDoraClassifierClient`
- httpx로 `CLICKBAIT_SERVICE_URL` 호출, retry + timeout 5초
- 실패 시 `ClassifierUnavailable` raise → A4 collection 측에서 ClickbaitResult error + TopicLinkageError 기록
- ClickbaitResult INSERT (model_name, adapter_type, decision, confidence, evaluated_at)

### 3. P0-1 미해결 시 stub
- 사용자 DoRA 모듈 경로 공유 전이면 stub 응답: 모든 입력에 `decision="clean", confidence=0.5` 고정 반환
- `services/clickbait-detector/Dockerfile`을 `python:3.12-slim` + 단순 FastAPI 응답으로 임시. 모듈 공유 후 교체.

### 4. 운영 통계
- 매일 자정 cron: `ClickbaitResult` 테이블 → Redis 24h cache (`clickbait:stats:{date}`)
- A10 admin이 `/admin/clickbait/stats`에서 read

### 5. docker-compose 통합
- `clickbait-detector` 서비스 healthcheck 추가
- `CLICKBAIT_SERVICE_URL=http://clickbait-detector:8100` 환경변수

## 헌법 (재강조)

- **2차 문헌(tech_news content_type)에만 적용**. 학술·빅테크 공식 채널은 통과 X.
- **사용자 화면에 confidence·decision 노출 금지** (FR-32). 통계는 admin 전용.
- **DoRA 모듈 가중치는 git 미포함** — `models/` 볼륨 mount 또는 시연 시 download.
- **NFR-09 충족 검증은 별도 dataset** (사용자 보유 validation set). 본 세션 범위 외.

## 검증

```bash
docker compose up -d clickbait-detector
curl -X POST http://localhost:8100/classify -d '{"document_id":"...","title":"충격! 놀라운 LLM 비밀","body":"...","source_name":"네이버뉴스","source_type":"tech_news","language":"ko","meta":{}}' -H "Content-Type: application/json"
# {decision: "clickbait" | "clean", confidence: 0.X, ...}

curl http://localhost:8100/health
# {status: "ok", model_loaded: true}

# 통합: A4 collection이 tech_news 1건 fetch → A5 classify → ClickbaitResult INSERT
docker compose exec api python -c "from app.clickbait_client import classify; ..."

mypy --strict backend/app/clickbait_client services/clickbait-detector/app
ruff check
pytest backend/tests/clickbait services/clickbait-detector/tests -v
```

## 출력 형식

기본 + 추가:
- DoRA 모듈 통합 상태 (실모듈 vs stub)
- ClickbaitResult fixture 검증 (5건 정도 분류 결과)
- 통계 캐시 동작 확인
- P0-1 해결 후 교체할 부분 list
