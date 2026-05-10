# clickbait_module

낚시성(clickbait) 분류기 — `A.X-4.0-Light` + DoRA 어댑터를 vLLM으로 서빙하는 자체 FastAPI 서비스.

backend는 `CLICKBAIT_SERVICE_URL` env로 본 서비스를 호출. 호스팅·transport(자체 도커 / 외부 GPU / 터널 등)는 운영 시점에 결정.

입출력 계약 SOR: [`../docs/algorithms/clickbait-integration.md`](../docs/algorithms/clickbait-integration.md) §인터페이스 계약.

## 디렉토리

```
clickbait_module/
├── app/                    # 운영 코드 (FastAPI + vLLM AsyncLLMEngine)
├── scripts/merge_adapter.py # DoRA → base 머지 (1회 실행)
├── tests/                  # GPU 없이 돌 수 있는 contract/shim 테스트
├── adapter/                # DoRA 어댑터 + run_meta.json (가중치 .gitignore)
├── reference/              # 참고용 — 운영 코드 아님
├── requirements.txt
├── .env.example
└── README.md
```

## 1. DoRA → base 머지 (1회)

vLLM은 일반 base model로 로드하므로 DoRA scaling을 base에 사전 머지한다.

```bash
python -m clickbait_module.scripts.merge_adapter \
    --base skt/A.X-4.0-Light \
    --adapter clickbait_module/adapter \
    --output ./merged-clickbait
```

- `--base` = [`skt/A.X-4.0-Light`](https://huggingface.co/skt/A.X-4.0-Light) HF id 또는 로컬 경로
- 어댑터 가중치(`adapter_model.safetensors`)는 본 repo에 미포함(`.gitignore`의 `*.safetensors`) — 별도 공유
- 머지 결과는 약 14GB(bfloat16). 디스크 여유 확인.
- 스크립트가 `chat_template.jinja` + `run_meta.json`을 출력 디렉토리에 함께 복사 → 학습 시 prompt 템플릿과 token id(`id0=56`, `id1=57`) 보존.

## 2. 환경변수 설정

```bash
cp clickbait_module/.env.example clickbait_module/.env
# MERGED_MODEL_PATH=./merged-clickbait 로 갱신
```

A100 40GB 단일 GPU 가정 default. tensor_parallel_size=1, gpu_memory_utilization=0.9, max_num_seqs=256, enable_prefix_caching=true.

## 3. 서버 실행

```bash
cd clickbait_module
uvicorn app.main:app --host 0.0.0.0 --port 8100
```

uvicorn worker는 1개 (vLLM이 한 GPU에 한 프로세스). 동시 요청 처리는 vLLM AsyncLLMEngine + continuous batching이 담당.

## 4. Smoke test

```bash
curl http://localhost:8100/health
# {"status":"ok","model_loaded":true,"stub_mode":false}

curl -X POST http://localhost:8100/classify \
    -H "Content-Type: application/json" \
    -d '{"document_id":"00000000-0000-0000-0000-000000000000","title":"충격!","body":"본문","source_name":"네이버뉴스","source_type":"tech_news","language":"ko","meta":{}}'
# {"decision":"clean","confidence":0.7,"model_name":"ax-4.0-light-dora-clickbait-v1","adapter_type":"dora","evaluated_at":"..."}
```

## Stub 모드

GPU/모델 없이 contract 검증용:

```bash
STUB_MODE=true uvicorn app.main:app --port 8100
```

또는 `STUB_MODE=false`라도 vLLM 부트 실패 시 자동 stub 진입(`decision="clean"`, `confidence=0.5`). 관리자 콘솔이 `/health`의 `model_loaded=false` + `error` 필드로 감지.

## 본문 길이 정책

본 모듈은 body를 자르지 않는다. body가 길어 vLLM `max_model_len` 초과 시 `/classify`가 503으로 응답하고, backend가 재판정/제외/운영자 로그 흐름으로 처리한다 ([`../docs/algorithms/clickbait-integration.md`](../docs/algorithms/clickbait-integration.md) §적용 정책).

스키마 레벨에선 [contract](../docs/algorithms/clickbait-integration.md) 정합 위해 `body` ≤ 8000자 제약을 유지한다.

## 테스트

```bash
cd clickbait_module
pip install pytest fastapi httpx pydantic pydantic-settings
pytest tests -v
```

GPU 없이 STUB_MODE 강제로 통과:

- `test_shim` — derive_category, to_classify_response, build_prompt prompt 구조 검증
- `test_health` — `/health` stub 모드 응답 형태
- `test_classify_stub` — `/classify` stub 응답 형태 + body max_length 검증

실 모델 추론 검증(NFR-09 정확도)은 머지된 모델 + GPU 환경에서 별도 수행 (사용자 보유 validation set 사용).

## reference/

학습 시 prompt 산식 검증에 사용한 참고용 예시 코드. **운영 코드 아님.**

- `ax4_clickbait_scorer.py` — transformers 기반 단일 기사 점수기. 운영은 vLLM AsyncLLMEngine으로 대체.
- `clickbait_preprocess.py` — 학습 원본 JSON 평탄화. 운영 입력은 backend가 직접 contract 형태로 보냄.

`app/shim.py`의 prompt 빌드는 `reference/ax4_clickbait_scorer.py`의 `build_article_text` + `build_messages_for_binary`와 글자 한 자도 다르지 않다. DoRA가 본 템플릿 전제로 학습됐으므로 prompt 변경 시 분류 정확도 무효.
