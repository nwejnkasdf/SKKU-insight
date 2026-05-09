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
    result_id: UUID
    document_id: UUID
    document_title: str
    model_name: str
    adapter_type: Literal["dora"]
    decision: Literal["clickbait", "clean"]
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
```

## 권한 매트릭스

| 엔드포인트 | super | operator | read_only |
|---|---|---|---|
| GET 통계/잡/사용자 | yes | yes | yes |
| POST reprocess | yes | yes | no |
| PATCH source toggle | yes | yes | no |
| 사용자 점수 열람 | yes | yes | no (마스킹) |
| 관리자 추가/제거 | yes | no | no |

## 비즈니스 룰

- 모든 `/admin/*` 응답은 `aud="admin"` 클레임 검증 (FR-60). 일반 사용자 토큰 → 403 즉시.
- 부트스트랩 admin은 첫 로그인 시 `must_change_password=true`로 강제 비번 변경.
- 사용자 이메일은 운영자 권한에서는 부분 마스킹 (예: `g***d@gmail.com`). super만 전체 노출.
- ClickbaitStats는 매일 자정에 미리 계산해 캐시 (Redis 24h TTL).

## 오류 응답

| code | HTTP | 의미 |
|---|---|---|
| `admin.unauthorized` | 403 | 일반 사용자 토큰 |
| `admin.role_insufficient` | 403 | role이 부족 |
| `admin.must_change_password` | 409 | 부트스트랩 직후 |
| `admin.reprocess_already_queued` | 409 | 동일 잡 재실행 진행 중 |
