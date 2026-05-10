# A5 — Clickbait Detector (Phase 1)

> 작업 디렉토리: ``
> **사전조건**: A2 backend + A4 collection 완료. **P0-1 해결됨 (2026-05-11)** — 모듈 위치 `clickbait_module/`, 서빙 엔진 vLLM, 호스팅·transport는 운영 결정. 자세히는 [`../docs/decision-backlog.md`](../docs/decision-backlog.md) P0-1 + P1-8 + P2-7.

## 너의 역할

사용자가 보유한 DoRA 파인튜닝된 `A.X-4.0-Light` 낚시성 탐지 모듈을 자체 FastAPI 서비스로 wrapping (vLLM 기반, 호스팅·transport는 운영 결정). 백엔드는 httpx로 `CLICKBAIT_SERVICE_URL` 호출.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `docs/algorithms/clickbait-integration.md` (전체)
- `docs/data/schema.md` (ClickbaitResult 부분)
- `docs/sdd/architecture.md` (Clickbait DoRA 컴포넌트)
- `docs/decision-backlog.md` (P0-1 항목)

## 산출

### 1. `clickbait_module/` (default) 또는 `services/clickbait-detector/` (자체 도커 호스팅 옵션)
- 모듈 위치 = `clickbait_module/`. **서빙 엔진 = vLLM** (DoRA를 base에 사전 머지 후 일반 base로 로드 + continuous batching).
- `clickbait_module/app/main.py` — FastAPI `/classify` (POST), `/health` (GET). vLLM `AsyncLLMEngine`을 lifespan에서 로드 (DoRA 사전 머지된 일반 base 모델). 머지 스크립트는 `clickbait_module/scripts/merge_adapter.py`.
- 호스팅·transport는 운영 결정. 자체 도커 호스팅 시 `services/clickbait-detector/Dockerfile`로 분리 가능 (옵션). [`../docs/algorithms/clickbait-integration.md`](../docs/algorithms/clickbait-integration.md) §호스팅·transport 추상화 참조.
- 입출력 스키마는 `algorithms/clickbait-integration.md` "인터페이스 계약" 그대로
- 모델 메타: `model_name="ax-4.0-light-dora-clickbait-v1"`, `adapter_type="dora"`
- vLLM·DoRA 호환성 검증 (P1-8) + logprob 추출 방식 결정 (P2-7)을 코드 작업 시작 시 1회 수행

### 2. backend `app/clickbait_client/`
- `ClickbaitClassifier` Protocol 구현체 `AxDoraClassifierClient`
- httpx로 `CLICKBAIT_SERVICE_URL` 호출, retry + timeout 5초
- 실패 시 `ClassifierUnavailable` raise → A4 collection 측에서 ClickbaitResult error + TopicLinkageError 기록
- ClickbaitResult INSERT (model_name, adapter_type, decision, confidence, evaluated_at)

### 3. Stub 응답 (외부 서비스 다운 / vLLM 부트 실패 / 로컬 디버그 대비)
- `STUB_MODE=true` env 또는 모델 로드 실패 시 stub 응답: 모든 입력에 `decision="clean", confidence=0.5` 고정 반환
- 로컬 디버그(GPU 없음) 시에도 동일 stub 모드로 contract 검증 가능

### 4. 운영 통계
- 매일 자정 cron: `ClickbaitResult` 테이블 → Redis 24h cache (`clickbait:stats:{date}`)
- A10 admin이 `/admin/clickbait/stats`에서 read

### 5. backend 통합 (transport 무관)
- backend는 `CLICKBAIT_SERVICE_URL` env로만 본 서비스를 호출. transport·호스팅과 무관하게 동일 계약 충족 시 swap 가능.
- 자체 도커 호스팅 시: docker-compose에 `clickbait-detector` 서비스 추가 + `CLICKBAIT_SERVICE_URL=http://clickbait-detector:8100`. 외부 호스팅 시: `CLICKBAIT_SERVICE_URL`을 외부 URL로 설정.
- backend `app/clickbait_client/`는 transport-agnostic — URL env만 본다.

## 헌법 (재강조)

- **2차 문헌(tech_news content_type)에만 적용**. 학술·빅테크 공식 채널은 통과 X.
- **사용자 화면에 confidence·decision 노출 금지** (FR-32). 통계는 admin 전용.
- **DoRA 모듈 가중치는 git 미포함** (`*.safetensors` gitignore) — HuggingFace Hub private repo에서 `merge_adapter.py` 실행 시 받음. 자체 도커 호스팅 옵션 시엔 `./models:/models:ro` 볼륨 mount.
- **NFR-09 충족 검증은 별도 dataset** (사용자 보유 validation set). 본 세션 범위 외.

## 검증

```bash
# 자체 도커 호스팅 시: docker compose up -d clickbait-detector
# 외부 호스팅 시: CLICKBAIT_SERVICE_URL을 외부 URL로 설정 후 backend에 전달

curl -X POST $CLICKBAIT_SERVICE_URL/classify -d '{"document_id":"...","title":"충격! 놀라운 LLM 비밀","body":"...","source_name":"네이버뉴스","source_type":"tech_news","language":"ko","meta":{}}' -H "Content-Type: application/json"
# {decision: "clickbait" | "clean", confidence: 0.X, ...}

curl $CLICKBAIT_SERVICE_URL/health
# {status: "ok", model_loaded: true}

# 통합: A4 collection이 tech_news 1건 fetch → A5 classify → ClickbaitResult INSERT
docker compose exec api python -c "from app.clickbait_client import classify; ..."

mypy --strict backend/app/clickbait_client clickbait_module/app
ruff check
pytest backend/tests/clickbait clickbait_module/tests -v
```

## 출력 형식

기본 + 추가:
- DoRA 모듈 통합 상태 (실모듈 vs stub)
- ClickbaitResult fixture 검증 (5건 정도 분류 결과)
- 통계 캐시 동작 확인
- 잔여 운영 작업 list (예: GPU 환경 머지 + sanity check)
