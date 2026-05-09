# API: Collection

본 파일은 사용자별 일일 수집 잡 API 명세이다. 사용자에게 보이는 부분과 관리자 콘솔 부분이 모두 포함된다. 관리자 전용 부분은 [`admin.md`](admin.md)에서 다시 참조한다. 관련 FR: FR-21~29.

> **API 통신 규약**: [`../sdd/api-conventions.md`](../sdd/api-conventions.md) 따름. list endpoint는 PagedResponse envelope.

## 베이스

- 기본 경로: `/collection`
- 일반 사용자: 자기 잡 상태 조회 + 강제 트리거 (rate limit 엄격)
- 관리자: 모든 사용자 잡 상태, 통계, 재실행

## 엔드포인트 표

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| GET | `/collection/jobs/me` | 자기 최근 수집 잡 상태 | user |
| POST | `/collection/jobs/me/run-now` | 강제 트리거 (시연용. 1/시간/사용자) | user |
| GET | `/admin/collection/jobs` | 모든 잡 (필터링) | admin |
| GET | `/admin/collection/jobs/{job_id}` | 잡 상세 + 실패 로그 | admin |
| POST | `/admin/collection/jobs/{job_id}/reprocess` | 재실행 요청 | admin (UC-05) |
| GET | `/admin/collection/sources` | 소스 레지스트리 + 활성 상태 | admin |
| PATCH | `/admin/collection/sources/{source_id}` | 소스 활성/비활성 토글 | admin |
| GET | `/admin/collection/stats` | 일일 수집 성공률, 사용자별 분포 | admin |

## 스키마

```python
JobStatus = Literal["queued", "running", "succeeded", "failed", "skipped"]
JobType = Literal["daily_collect", "leaf_lifecycle", "merge_evaluation", "summary_generation"]

class CollectionJobView(BaseModel):
    job_id: UUID
    user_id: UUID
    source_id: UUID | None       # 다중 소스를 하나의 잡으로 처리하는 경우 None
    target_cso_topic_id: UUID | None
    target_leaf_topic_id: UUID | None
    job_type: JobType
    status: JobStatus
    failure_reason: str | None
    retry_count: int
    started_at: datetime | None
    finished_at: datetime | None

class CollectionJobMeResponse(BaseModel):
    latest: CollectionJobView | None
    history: list[CollectionJobView]   # 지난 7일

class RunNowResponse(BaseModel):
    job_id: UUID
    status: Literal["queued"]
    eta_seconds: int

class ReprocessRequestPayload(BaseModel):
    reason: str | None

class ReprocessRequestView(BaseModel):
    request_id: UUID
    admin_id: UUID
    job_id: UUID
    requested_at: datetime
    status: Literal["queued", "running", "succeeded", "failed"]
    result_message: str | None

class SourceView(BaseModel):
    source_id: UUID
    name: str
    source_type: Literal["academic", "vendor_blog", "tech_news"]
    url: str
    trust_level: Literal["high", "medium", "low"]
    enabled: bool
    last_success_at: datetime | None

class CollectionStatsResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    success_rate: float            # 0..1, NFR-10 기준 0.95 이상
    total_jobs: int
    failed_jobs: int
    failures_by_source: dict[str, int]
```

## 비즈니스 룰

- 일일 수집은 `COLLECTION_CRON` 환경변수에 따라 사용자별로 스케줄. 동일 사용자에 대해 동시 실행 1건만 허용 (Redis lock).
- `failure_reason` 텍스트는 외부에 그대로 노출하지 않고, 관리자 콘솔에서만 표시 (NFR-08).
- `POST /admin/collection/jobs/{job_id}/reprocess`는 ReprocessRequest를 생성하고 Job을 큐에 다시 enqueue. ReprocessRequest는 별도 테이블 (UC-05, FR-65).
- `GET /admin/collection/stats`의 success_rate가 NFR-10의 95% 미만이면 응답에 `alert: "below_sla"` 플래그 포함 (관리자 대시보드에서 빨강 표시).

## 오류 응답

| code | HTTP | 의미 |
|---|---|---|
| `collection.already_running` | 409 | 동일 사용자 잡 진행 중 |
| `collection.job_not_found` | 404 | |
| `collection.source_disabled` | 422 | 비활성 소스 |
| `collection.rate_limited` | 429 | run-now 1/시간 위반 |
