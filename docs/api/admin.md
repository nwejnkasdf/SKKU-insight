# API: Admin

본 파일은 관리자 웹 콘솔 전용 API 명세이다. 일반 사용자 API와 권한이 분리되어야 한다 (FR-60, NFR-22). 관련 FR: FR-60~65. 관련 NFR: NFR-08, NFR-22.

> **API 통신 규약**: [`../sdd/api-conventions.md`](../sdd/api-conventions.md) 따름. list endpoint는 §6 PagedResponse envelope. admin은 §13 마스킹 정책에서 super/operator/read_only 권한별 차이 적용.

## 베이스

- 기본 경로: `/admin`
- 인증: admin role의 access_token (JWT 클레임 `aud="admin"`). 일반 사용자 토큰은 모든 `/admin/*` 호출에서 403.

## 엔드포인트 표

### 인증
| Method | Path | 설명 |
|---|---|---|
| POST | `/admin/auth/login` | 관리자 로그인 |
| POST | `/admin/auth/refresh` | 토큰 갱신 |
| POST | `/admin/auth/logout` | 로그아웃 |
| POST | `/admin/auth/change-password` | 비밀번호 변경 (부트스트랩 시 강제) |

### 수집 (collection.md와 중복 표기)
| Method | Path | 설명 |
|---|---|---|
| GET | `/admin/collection/jobs` | 잡 목록 (필터: status, user_id, source_id, since) |
| GET | `/admin/collection/jobs/{job_id}` | 잡 상세 |
| POST | `/admin/collection/jobs/{job_id}/reprocess` | 재실행 (UC-05) |
| GET | `/admin/collection/sources` | 소스 레지스트리 |
| PATCH | `/admin/collection/sources/{source_id}` | 소스 활성/비활성 |
| GET | `/admin/collection/stats` | 통계 |

### 낚시성 통계 (FR-33, FR-63)
| Method | Path | 설명 |
|---|---|---|
| GET | `/admin/clickbait/stats` | 일일 낚시성 통계 |
| GET | `/admin/clickbait/results` | 결과 목록 (필터링) |

### 토픽 연결 오류 (FR-64)
| Method | Path | 설명 |
|---|---|---|
| GET | `/admin/topic-linkage/errors` | 토픽 연결 실패 목록 |
| POST | `/admin/topic-linkage/errors/{error_id}/retry` | 재처리 |

### 사용자 (NFR-04 우회: 관리자만 점수 열람)
| Method | Path | 설명 |
|---|---|---|
| GET | `/admin/users` | 사용자 목록 (페이징) |
| GET | `/admin/users/{user_id}/interest-state` | 사용자 관심 상태 (점수 포함, 관리자만) |
| GET | `/admin/users/{user_id}/events` | 사용자 행동 로그 |
| POST | `/admin/users/{user_id}/collection/run-now` | 동의 활성 사용자 문서 수집 즉시 실행 |

### 재실행 요청 이력
| Method | Path | 설명 |
|---|---|---|
| GET | `/admin/reprocess-requests` | 재실행 요청 목록 |
| GET | `/admin/reprocess-requests/{request_id}` | 단건 |

## 스키마

```python
class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str

class AdminTokenPair(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    must_change_password: bool   # 부트스트랩 직후 true

class AdminRole(str, Enum):
    SUPER = "super"
    OPERATOR = "operator"
    READ_ONLY = "read_only"

class AdminMeResponse(BaseModel):
    admin_id: UUID
    email: EmailStr
    role: AdminRole
    status: Literal["active", "suspended"]
    last_login_at: datetime | None

class ChangeAdminPasswordRequest(BaseModel):
    current_password: str
    new_password: str

class AdminRefreshRequest(BaseModel):
    refresh_token: str

class AdminLogoutRequest(BaseModel):
    # codex C-2: 로그아웃 시 admin refresh token 함께 폐기 (decision-backlog C-13).
    refresh_token: str | None = None

# === 수집 (관리자 전용 — collection.md 의 사용자용 schema 와 별도) ===

class ReprocessRequestPayload(BaseModel):
    """`POST /admin/collection/jobs/{id}/reprocess` 요청 본문."""
    reason: str | None

class ReprocessRequestView(BaseModel):
    """재실행 요청 row (UC-05, FR-65)."""
    request_id: UUID
    admin_id: UUID
    job_id: UUID
    requested_at: datetime
    status: Literal["queued", "running", "succeeded", "failed"]
    result_message: str | None

class SourceView(BaseModel):
    """소스 레지스트리 row."""
    source_id: UUID
    name: str
    source_type: SourceType   # contracts.py SOR enum (sdd/contracts.md §2)
    url: str
    trust_level: Literal["high", "medium", "low"]
    enabled: bool
    last_success_at: datetime | None

class SourceTogglePatch(BaseModel):
    """`PATCH /admin/collection/sources/{id}` — 활성/비활성 토글."""
    enabled: bool

class CollectionStatsResponse(BaseModel):
    """일일 수집 통계. NFR-10 기준 success_rate < 0.95 시 alert='below_sla'."""
    period_start: datetime
    period_end: datetime
    success_rate: float
    total_jobs: int
    failed_jobs: int
    failures_by_source: dict[str, int]
    alert: Literal["below_sla"] | None

# === 낚시성 통계 ===

class ClickbaitStatsResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    total_evaluated: int
    clickbait_count: int
    clean_count: int
    excluded_per_user_avg: float
    by_source: dict[str, ClickbaitBySource]

class ClickbaitBySource(BaseModel):
    source_name: str
    evaluated: int
    clickbait: int

class ClickbaitResultView(BaseModel):
    """관리자 콘솔에 노출되는 낚시성 판정 결과. decision 은 `error` 도 포함 (분류 실패 시 운영자 가시성)."""
    result_id: UUID
    document_id: UUID
    document_title: str
    model_name: str
    adapter_type: Literal["dora"]
    decision: Literal["clickbait", "clean", "error"]   # contracts.py ClickbaitDecision 일치 (3개)
    confidence: float
    evaluated_at: datetime

class TopicLinkageErrorView(BaseModel):
    error_id: UUID
    document_id: UUID
    expected_cso_topic_id: UUID | None
    error_message: str
    retry_count: int
    occurred_at: datetime

class AdminUserListItem(BaseModel):
    user_id: UUID
    email: EmailStr   # 마스킹: 운영자/읽기전용 권한은 부분 마스킹
    created_at: datetime
    consent_active: bool
    deletion_pending: bool

class AdminUserInterestState(BaseModel):
    user_id: UUID
    topics: list[AdminInterestTopicView]
    updated_at: datetime

class AdminInterestTopicView(BaseModel):
    cso_topic_id: UUID | None
    leaf_topic_id: UUID | None
    label: str
    long_score: float       # 관리자 콘솔에만 노출
    short_score: float
    bucket: Literal["high", "medium", "low", "neutral"]

# POST /admin/users/{user_id}/collection/run-now 는 collection.RunNowResponse 재사용
# class RunNowResponse(BaseModel):
#     job_id: UUID
#     eta_seconds: int
```

## 권한 매트릭스

| 엔드포인트 | super | operator | read_only |
|---|---|---|---|
| GET 통계/잡/사용자 | yes | yes | yes |
| POST reprocess | yes | yes | no |
| POST user collection run-now | yes | yes | no |
| PATCH source toggle | yes | yes | no |
| 사용자 점수 열람 | yes | yes | no (마스킹) |
| 관리자 추가/제거 | yes | no | no |

## 비즈니스 룰

- 모든 `/admin/*` 응답은 `aud="admin"` 클레임 검증 (FR-60). 일반 사용자 토큰 → 403 즉시.
- 부트스트랩 admin은 첫 로그인 시 `must_change_password=true`로 강제 비번 변경.
- **`must_change_password=true` 인 admin 의 `/admin/*` 호출은 409 `admin.must_change_password` 로 차단** (codex C-4, decision-backlog C-14). 예외 경로: `/admin/auth/change-password` + `/admin/auth/logout` 두 endpoint 만 통과. 비번 변경 후 `must_change_password=false` 로 갱신되면 다른 admin API 사용 가능.
- **`/admin/users` 응답의 email 마스킹 정확 규칙** (NFR-04):
  - `super` 권한: 전체 email 원문 그대로 노출
  - `operator` / `read_only` 권한: local part 길이에 따라
    - **길이 ≥ 2**: 첫글자 + `***` + 마지막글자 + `@` + 도메인 (예: `gywnd123@gmail.com` → `g***3@gmail.com`)
    - **길이 = 1**: 전체 local part 마스킹 fallback (예: `a@gmail.com` → `***@gmail.com`)
- `POST /admin/users/{user_id}/collection/run-now` 는 동의 활성 사용자에게만 허용한다. 내부적으로 사용자용 `trigger_run_now`와 같은 큐잉 로직을 재사용하므로 같은 사용자에 대해 이미 수집 중이면 409 `collection.already_running` 을 반환한다.
- ClickbaitStats는 매일 자정에 미리 계산해 캐시 (Redis 24h TTL).

## 오류 응답

| code | HTTP | 의미 |
|---|---|---|
| `admin.unauthorized` | 403 | 일반 사용자 토큰 |
| `admin.role_insufficient` | 403 | role이 부족 |
| `admin.must_change_password` | 409 | 부트스트랩 직후 |
| `admin.reprocess_already_queued` | 409 | 동일 잡 재실행 진행 중 |
| `collection.already_running` | 409 | 사용자 수집 잡이 이미 진행 중 |
