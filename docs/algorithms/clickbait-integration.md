# 알고리즘: 낚시성 탐지 통합 (DoRA 모듈 wrap)

본 파일은 사용자 보유 DoRA 파인튜닝 `A.X-4.0-Light` 낚시성 탐지 모듈을 시스템에 통합하는 인터페이스 계약을 정의한다. 관련 FR: FR-30~34. 관련 NFR: NFR-07, NFR-09. 관리자 통계 API는 [`../api/admin.md`](../api/admin.md).

## 결정

- 모듈은 자체 FastAPI 서비스로 운영. **서빙 엔진 = vLLM** (DoRA를 base에 사전 머지 후 일반 base로 로드 + continuous batching으로 동시 요청 처리).
- **호스팅 위치와 transport는 운영 결정**. 백엔드는 `CLICKBAIT_SERVICE_URL` env로 호출하므로 transport(docker internal / VPN / 공개 터널 등)와 호스팅(자체 도커 / 외부 GPU 등)에 무관.
- **2차 문헌(테크 뉴스)** 수집 단계의 1차 정제에만 사용. 학술 소스, 빅테크 공식 채널은 통과시키지 않는다 (FR-25 본 SRS 근거: "뉴스/기사성 2차 문서").
- 모듈 위치 = `clickbait_module/`. 가중치 호스팅 = HuggingFace Hub private repo. (P0-1 해결됨, [`../decision-backlog.md`](../decision-backlog.md))

## 인터페이스 계약

### 호출 (백엔드 → clickbait 서비스)

```http
POST {CLICKBAIT_SERVICE_URL}/classify HTTP/1.1
Content-Type: application/json

{
  "document_id": "uuid",
  "title": "string",
  "body": "string (최대 8000자, 잘림 허용)",
  "source_name": "string",
  "source_type": "tech_news",
  "language": "ko" | "en",
  "meta": {
    "published_at": "iso8601",
    "url": "https://...",
    "summary": "string (optional)"
  }
}
```

### 응답

```json
{
  "decision": "clickbait" | "clean" | "error",
  "confidence": 0.0,
  "model_name": "ax-4.0-light-dora-clickbait-v1",
  "adapter_type": "dora",
  "evaluated_at": "iso8601"
}
// decision="error" — 분류기 자체 실패 (모델 미로드, max_model_len 초과 등).
// backend 는 ClickbaitResult INSERT (감사용) + 추천 후보 제외 (보수적 처리).
// ClickbaitDecision enum (sdd/contracts.md §2) 3 값과 일치.
```

### 헬스체크

```
GET {CLICKBAIT_SERVICE_URL}/health
→ 200 {"status":"ok","model_loaded":true,"stub_mode":false,"error":null}
```

`status`는 `"ok"|"degraded"`, `model_loaded`는 vLLM 부트 성공 여부, `stub_mode`는 stub 응답 모드 활성 여부, `error`는 부트 실패 시 메시지(정상 시 null). backend healthcheck는 `model_loaded` 또는 `stub_mode` 둘 중 하나가 true면 서비스 가용으로 간주.

## 적용 정책

```python
async def filter_clickbait(documents: list[Document], classifier: ClickbaitClassifier, db) -> list[Document]:
    survivors = []
    for d in documents:
        if d.content_type != "tech_news":
            survivors.append(d)
            continue
        try:
            decision = await classifier.classify(d)
        except ClassifierUnavailable:
            db.add(ClickbaitResult(
                document_id=d.id,
                model_name="unknown",
                adapter_type="dora",
                decision="error",
                confidence=0.0,
                evaluated_at=now(),
            ))
            # 추천 후보로 사용하지 않음. 재처리 대상으로 표기.
            db.add(TopicLinkageError(document_id=d.id, error_message="clickbait_classifier_unavailable"))
            continue
        db.add(ClickbaitResult(
            document_id=d.id,
            model_name=decision.model_name,
            adapter_type=decision.adapter_type,
            decision=decision.decision,
            confidence=decision.confidence,
            evaluated_at=decision.evaluated_at,
        ))
        if decision.decision == "clean":
            survivors.append(d)
        # else: 추천 후보 제외 (FR-31)
    return survivors
```

## 사용자 노출 정책 (FR-32)

ClickbaitResult.confidence와 decision은 **사용자 화면에 절대 노출하지 않는다**. 추천 카드와 문서 상세 응답은 단지 "이 문서는 추천에 사용되었다 / 사용되지 않았다" 결과만 반영.

관리자 콘솔(UI-06)은 통계와 단건 결과 모두 노출 (FR-33). 권한별 마스킹은 [`../api/admin.md`](../api/admin.md) 권한 매트릭스 참조.

## 통계 집계

`ClickbaitResult` 테이블에서 매일 자정에 다음을 미리 계산해 Redis 24h 캐시:

- 일자별 total_evaluated, clickbait_count, clean_count
- 사용자당 평균 제외 문서 수 (excluded_per_user_avg)
- 소스별 분포 (`by_source`)

API: `GET /admin/clickbait/stats` ([`../api/admin.md`](../api/admin.md)).

## 모듈 외부 인터페이스 가정

- `clickbait_module/app/` — FastAPI 래퍼 (`/classify`, `/health`) + vLLM `LLM` 인스턴스. 호스팅(자체 도커 / 외부 GPU 호스팅 등)과 transport는 운영 결정.
- 모델 메타: `model_name="ax-4.0-light-dora-clickbait-v1"`, `adapter_type="dora"`
- 1회 추론 SLA: P1-3 default 5초 ([`../decision-backlog.md`](../decision-backlog.md)). 초과 시 비동기 큐 전환.
- validation 메트릭: accuracy, recall, AUROC, F1 모두 98%대 (NFR-09 가정)

## 서빙 엔진 (vLLM)

낚시성 분류기는 **vLLM**을 서빙 엔진으로 사용한다.

- **DoRA → base 머지 후 일반 base로 서빙**: vLLM의 multi-LoRA serving(`LoRARequest` 기반 어댑터 hot-swap)은 사용하지 않는다. 대신 사전에 `peft.PeftModel.merge_and_unload()`로 DoRA scaling을 base 가중치에 머지한 결과를 vLLM이 일반 base 모델로 로드한다. 머지 스크립트는 `clickbait_module/scripts/merge_adapter.py`.
- **continuous batching**: 다수 동시 요청을 PagedAttention으로 효율 처리. 10~20명 동시 사용자가 수집 직후 일제히 분류 호출해도 throughput 안정.
- **next-token logprob**: chat template prefix의 다음 토큰 "0" vs "1" 확률을 vLLM의 `SamplingParams(max_tokens=1, logprobs=K, temperature=0.0)` 출력에서 추출 후 2-class softmax. 학습 시점 산식과 동일성 보존. token id(`id0=56`, `id1=57`)는 `adapter/run_meta.json`에서 권위 로드.

## 호스팅·transport 추상화

백엔드 호출자는 본 모듈의 호스팅 위치와 transport를 알지 못한다. `CLICKBAIT_SERVICE_URL` 환경변수가 가리키는 URL이 동일 계약(`POST /classify`, `GET /health`)을 충족하는 한, 호스팅(도커 컴포즈 internal / 외부 GPU / 자체 호스팅)과 transport(docker internal hostname / VPN / 공개 터널)는 운영 시점에 자유롭게 결정·교체 가능하다.

→ 본 모듈 변경이나 운영 결정 변경이 backend `app/clickbait_client/` 코드에 영향을 주지 않는다 (URL env 한 줄로 swap).
