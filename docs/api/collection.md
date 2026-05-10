# API: Collection

본 파일은 **사용자용 수집 잡 API**의 명세이다. 관리자 콘솔 영역(`/admin/collection/*` 6개)은 [`admin.md §수집`](admin.md)이 단일 SOR이며 본 파일은 본문에서 다루지 않는다 (사용자 결정 2026-05-11 — endpoint·schema 중복 제거). 관련 FR: FR-21~29.

> **API 통신 규약**: [`../sdd/api-conventions.md`](../sdd/api-conventions.md) 따름. list endpoint는 PagedResponse envelope.

> **관리자 영역은 별도 SOR**: `/admin/collection/jobs`, `/admin/collection/jobs/{id}`, `/admin/collection/jobs/{id}/reprocess`, `/admin/collection/sources`, `/admin/collection/sources/{id}` (PATCH), `/admin/collection/stats` 6 endpoint와 그 전용 schema(`ReprocessRequestPayload`, `ReprocessRequestView`, `SourceView`, `SourceTogglePatch`, `CollectionStatsResponse`)는 모두 [`admin.md §스키마`](admin.md) 참조. 본 파일은 사용자용 2 endpoint와 양 영역이 공유하는 schema(`JobStatus`, `JobType`, `CollectionJobView`, `CollectionJobPublicView`, `CollectionJobMeResponse`, `RunNowResponse`)만 정의한다.

## 베이스

- 기본 경로: `/collection`
- 인증: access_token (`aud="user"`) + 동의 활성

## 엔드포인트 표 (사용자용)

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| GET | `/collection/jobs/me` | 자기 최근 수집 잡 상태 | user |
| POST | `/collection/jobs/me/run-now` | 강제 트리거 (시연용. 1/시간/사용자) | user |

## 스키마

```python
JobStatus = Literal["queued", "running", "succeeded", "failed", "skipped"]
JobType = Literal["daily_collect", "leaf_lifecycle", "merge_evaluation", "summary_generation"]

class CollectionJobView(BaseModel):
    """관리자 응답용 — failure_reason 포함 (NFR-08, 사용자 응답에는 노출 X)."""
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

class CollectionJobPublicView(BaseModel):
    """사용자 응답용 — failure_reason 마스킹 (NFR-08)."""
    job_id: UUID
    user_id: UUID
    source_id: UUID | None
    target_cso_topic_id: UUID | None
    target_leaf_topic_id: UUID | None
    job_type: JobType
    status: JobStatus
    retry_count: int
    started_at: datetime | None
    finished_at: datetime | None

class CollectionJobMeResponse(BaseModel):
    """`GET /collection/jobs/me` 응답. 사용자 노출이라 PublicView 사용."""
    latest: CollectionJobPublicView | None
    history: list[CollectionJobPublicView]   # 지난 7일

class RunNowResponse(BaseModel):
    job_id: UUID
    status: Literal["queued"]
    eta_seconds: int
```

## 비즈니스 룰

- 일일 수집은 `COLLECTION_CRON` 환경변수에 따라 사용자별로 스케줄. 동일 사용자에 대해 동시 실행 1건만 허용 (Redis lock, `lock:collection:{user_id}`).
- `CollectionJobView` (admin 응답) 는 `failure_reason` 포함, `CollectionJobPublicView` (사용자 응답) 는 NFR-08 따라 마스킹.
- 관리자 영역(`/admin/collection/*`) 비즈니스 룰은 [`admin.md §비즈니스 룰`](admin.md) 참조.

## 오류 응답

| code | HTTP | 의미 |
|---|---|---|
| `collection.already_running` | 409 | 동일 사용자 잡 진행 중 |
| `collection.job_not_found` | 404 | |
| `collection.source_disabled` | 422 | 비활성 소스 |
| `collection.rate_limited` | 429 | run-now 1/시간 위반 |
