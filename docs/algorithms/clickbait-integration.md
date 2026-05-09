# 알고리즘: 낚시성 탐지 통합 (DoRA 모듈 wrap)

본 파일은 사용자 보유 DoRA 파인튜닝 `A.x 4.0 light` 낚시성 탐지 모듈을 시스템에 통합하는 인터페이스 계약을 정의한다. 관련 FR: FR-30~34. 관련 NFR: NFR-07, NFR-09. 관리자 통계 API는 [`../api/admin.md`](../api/admin.md).

## 결정

- 모듈은 `services/clickbait-detector` 컨테이너로 wrapping (자체 FastAPI). 백엔드는 `httpx`로 호출.
- **2차 문헌(테크 뉴스)** 수집 단계의 1차 정제에만 사용. 학술 소스, 빅테크 공식 채널은 통과시키지 않는다 (FR-25 본 SRS 근거: "뉴스/기사성 2차 문서").
- 모델 위치는 사용자 공유 예정. <!-- TODO: 사용자가 DoRA 모듈 경로(어댑터 weight, base model 위치)를 공유한 후 services/clickbait-detector/ 의 README와 Dockerfile에 반영 -->

## 인터페이스 계약

### 호출 (백엔드 → clickbait-detector)

```http
POST /classify HTTP/1.1
Host: clickbait-detector:8100
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
  "decision": "clickbait" | "clean",
  "confidence": 0.0,
  "model_name": "ax-4.0-light-dora-clickbait-v1",
  "adapter_type": "dora",
  "evaluated_at": "iso8601"
}
```

### 헬스체크

```
GET /health
→ 200 {"status":"ok","model_loaded":true}
```

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

## 모듈 외부 인터페이스 가정 (TODO 채워질 사항)

- `services/clickbait-detector/Dockerfile` — Python 3.10+ + transformers + base 모델 다운로드 + DoRA 어댑터 weights 로드
- `services/clickbait-detector/app/main.py` — FastAPI `/classify`, `/health` 엔드포인트
- 모델 메타: `model_name="ax-4.0-light-dora-clickbait-v1"`, `adapter_type="dora"`
- 1회 추론 SLA: <!-- TODO: GPU 가용성에 따라 결정. CPU만 가용하면 5초 이내 목표, 초과 시 비동기 큐 전환 -->
- validation 메트릭: accuracy, recall, AUROC, F1 모두 98%대 (NFR-09 가정)
