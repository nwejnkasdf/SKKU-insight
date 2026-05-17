# A4 — Collection (Phase 1) · v13 라운드 Topic-driven LLM Tool-use Pivot

> 작업 디렉토리: ``
> **사전조건**: A2 backend + A3 cso-topic 완료. 단 A5(clickbait)·A6(베이지안) 진행과 병렬 가능.
> **v13 라운드 pivot 반영본 (2026-05-11)** — 이전 push-from-sources 모델(6 source 어댑터)이 폐기됐고, LLM tool-use(web search) topic-driven pull 모델로 재정렬됨. 자세히는 [`docs/decisions.md §10`](../docs/decisions.md) + [`docs/decision-backlog.md` C-33](../docs/decision-backlog.md).

## 너의 역할

사용자별 일일 수집 잡 + **LLM tool-use 검색 호출 1경로** + Document/CollectionJob 영속 + jitter 스케줄러 + dedup. 6 source 어댑터(arXiv/OpenAlex/Semantic Scholar/DBLP/RSS/네이버BS4)는 **미구현** — `app/source_adapters/` 디렉토리 생성 X. 결과를 A5 clickbait 필터로 넘기는 경로는 **사용자가 News 소스 명시 활성화 시만** 동작 (default 비활성).

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- [`docs/decisions.md §10`](../docs/decisions.md) v13 라운드 결정 매트릭스 (Pivot SOR)
- [`docs/decision-backlog.md` C-33](../docs/decision-backlog.md) pivot 항목 + P1-6/P2-3/P2-4 무효 마킹 + 활성 P1·P2
- [`docs/srs/02-functional-requirements.md`](../docs/srs/02-functional-requirements.md) FR-22~25 식별자 보존 + v13 해석 박스
- [`docs/srs/03-nonfunctional-requirements.md`](../docs/srs/03-nonfunctional-requirements.md) NFR-25 self-summary 정합 박스
- [`docs/data/schema.md`](../docs/data/schema.md) Document·DocumentTopic·CollectionJob·Source·SourcePolicy·ClickbaitResult·TopicLinkageError + Document.raw 의미 갱신 박스
- [`docs/api/collection.md`](../docs/api/collection.md) endpoint·schema + v13 박스
- [`docs/algorithms/cso-mapping.md`](../docs/algorithms/cso-mapping.md) §v13 라운드 — Document ↔ cso_topic 매핑 단순화
- [`docs/algorithms/clickbait-integration.md`](../docs/algorithms/clickbait-integration.md) v13 라운드 발동 조건
- [`docs/algorithms/cso-topic-traversal.md`](../docs/algorithms/cso-topic-traversal.md) §6.1 (수집 대상 = active trace path ∪ 1-hop adjacent — A7 의존, 본 세션은 onboarding 선택 cluster fallback)
- [`docs/sdd/concurrency.md`](../docs/sdd/concurrency.md) §8 (jitter), §10 체크리스트
- [`docs/sdd/architecture.md`](../docs/sdd/architecture.md) collection-orchestrator (v13 박스)
- [`docs/ops/runbooks.md`](../docs/ops/runbooks.md) §1 (수집 실패 대응)

## 사용자 결정 매트릭스 (v13 라운드)

| 결정 | 값 |
|---|---|
| **수집 모델** | `LLMProvider.search_with_tools()` 단일 경로. user trace JSON 입력 → LLM 자율 query 결정 → web 검색 도구 호출 |
| **LLM provider** | `LLM_PROVIDER` env toggle. **v13 round 2 (2026-05-16) 결정**: `MockProvider` (default, CI/시연 fixture) + **`OpenAIAPIProvider` (정식, GPT-5.5 + Responses API + `web_search` tool)** 만 지원. Anthropic/OpenRouter/CodexOAuth 는 `search_with_tools` → `NotImplementedError`. lifespan `_validate_llm_provider` 가드 (`_SUPPORTED_A4_PROVIDERS = {"mock", "openai"}`) 가 boot 시 미지원 provider 차단 (S-08 fix) |
| **Query 구성** | LLM 이 trace JSON 통째 받아 스스로 query 결정 (agent-driven, prompt instruction 만) |
| **Source 테이블** | sentinel 1행 `llm_search` 추가 + 기존 `cold_start_pseudo`. publisher 정보는 `Document.raw` JSONB |
| **CollectionJob 단위** | (user × source) — source 가 sentinel `llm_search` 단일이라 실효 user 별 1건/회 |
| **Trigger** | daily cron (`COLLECTION_CRON=0 3 * * *`) + manual `POST /collection/jobs/me/run-now` (1/h). onboarding/login 자동 trigger 미사용 |
| **Jitter** | `deterministic hash(user_id, YYYYMMDD) % 300초`. 재현 가능 + uniform 분포 |
| **LLM 호출 패턴** | 1 call / 1 active leaf (= query 단위, Document 단위 아님). top-N=10 결과 |
| **CSO topic 매핑** | **자동 해결** — 검색 query 자체가 topic 이므로 `DocumentTopic.cso_topic_id = leaf 부모 cso_topic` 직접 사용. 별도 매핑 알고리즘 미구현 |
| **Dedup 우선순위** | DOI → canonical_url → URL 정규화(utm_*/fbclid/gclid 제거 + lowercase host) → title 정규화 + Levenshtein ≥ 0.90 |
| **Document.PK** | UUID v4. canonical_url partial unique index (NOT NULL 일 때만), doi 동일 |
| **외부(LLM) 실패** | FAILED/SKIPPED 구분 + RQ retry 3회 exponential (60s/300s/900s) |
| **/collection/jobs/me 응답** | cursor pagination, default limit=20 / max=100 |
| **/topics/{id}/documents endpoint** | **A4 가 본문 같이 채움** (cross-cutting) |
| **NFR-25 정합** | LLM 검색 prompt 에 "abstract 본인 말로 1~2문장 요약" instruction. Document.summary = LLM self-summary |
| **Clickbait** | 1차 시연 default 비활성. 사용자 News 소스 활성화 시만 post-filter |

## 산출

### 1. alembic 0003 — 신규 4 ORM 모델 + sentinel 시드

- `backend/alembic/versions/0003_a4_collection_tables.py`
  - 신규: `Document`, `DocumentTopic`, `CollectionJob`, `ClickbaitResult` 4 테이블
  - 신규 sentinel: `Source(name="llm_search", source_type="vendor_blog", trust_level="high", enabled=true)` 1행 `op.bulk_insert`
  - `cold_start_pseudo` 는 A2 alembic 0001 에서 이미 시드됨 (추가 X)

### 2. 신규 ORM 모델 (`backend/app/db/models/`)
- `document.py` — Document
- `document_topic.py` — DocumentTopic (M:N)
- `collection_job.py` — CollectionJob
- `clickbait_result.py` — ClickbaitResult (1차 비활성이지만 schema 보존)
- `__init__.py` export 추가

### 3. `LLMProvider.search_with_tools()` 확장

- `backend/app/llm_provider/protocol.py` — Protocol 메서드 추가:
  ```python
  async def search_with_tools(
      self,
      trace_json: dict[str, Any],
      leaf_label: str,
      *,
      top_n: int = 10,
      user_id: str | None = None,
  ) -> list[SearchResult]: ...
  ```
- `mock.py` — fixture 기반 deterministic 응답 (기존 `prompt_hash → fixture.json` 패턴 확장). `hash_prompt_search()` 가 `SYSTEM_PROMPT_VERSION` + `SYSTEM_PROMPT_TEMPLATE` 본문 SHA256 둘 다 포함 → 본문 1자 변경 시 fixture 자동 invalidate (R2-N01 fix)
- `openai.py` — Responses API + `web_search` tool. GPT-5.5 (reasoning 파라미터 OpenAI default 위임). 12 output items chain (5 reasoning + 5 web_search_call + 1 message). `response.json()` ValueError 도 `ProviderError` wrap (R2-S03 fix)
- ~~`anthropic.py` — Messages API web_search tool~~ — **v13 round 2 폐기**. `search_with_tools` 미구현, `NotImplementedError` 만. boot 시 lifespan 가드가 `LLM_PROVIDER=anthropic` 차단

### 4. Collection 모듈 신규 (`backend/app/collection/`)
- `llm_search.py` — LLM tool-use wrapper. trace+leaf 입력 → SearchResult list
- `dedup.py` — DOI / canonical_url / URL 정규화 / title Levenshtein 룰
- `orchestrator.py` — `run_collection_for_user(user_id)` 파이프라인 (load trace → for each leaf: search → dedup → INSERT → CollectionJob 갱신)

### 5. Endpoint 본문

- `backend/app/collection/router.py`:
  - `GET /collection/jobs/me` — cursor pagination
  - `POST /collection/jobs/me/run-now` — 1/h rate limit, RQ enqueue
- `backend/app/topic/router.py` `/topics/{id}/documents` 본문 (cross-cutting):
  - DocumentTopic JOIN Document WHERE cso_topic_id = topic_id
  - ORDER BY published_at DESC + cursor pagination + since 필터

### 6. Worker job 본문

- `backend/app/worker/jobs/collection.py` — `collection_job(user_id_str: str | None = None)`:
  - user_id 없으면 모든 active user 순회 (cron)
  - user_id 지정 시 단일 user (manual run-now)
  - deterministic jitter sleep (hash(user_id, today) % 300s)
  - sync 함수 (RQ 표준) — 내부에서 새 asyncio loop + session 생성

- `backend/app/worker/jobs/naver_cleanup.py` — **stub 유지** (decision-backlog P1-6 무효 마킹으로 등록 제거 또는 비활성). `app/scheduler.py` 에서 `naver_cleanup_job` 등록 제거.

### 7. 6 cross-check 갱신 (필요 시)

- `backend/app/contracts.py` 신규 RedisKey / ErrorCode 추가 시 `scripts/check_*.py` 도 같이 통과 확인
- 1차 시연은 캐시 미적용 → 신규 RedisKey 없을 예상

### 8. clickbait_module 비활성

- `backend/app/scheduler.py` 의 `naver_cleanup_job` 등록 코드 제거
- A5 호출 경로는 코드 보존하되 default 비활성 (사용자 News 활성화 시만)

## 헌법 (재강조)

- **임베딩 미사용**. 토픽 매핑은 LLM 검색 query = topic 자체로 자동 해결 ([`cso-mapping.md §v13`](../docs/algorithms/cso-mapping.md))
- **6 source 어댑터 미구현**. `app/source_adapters/` 디렉토리 생성 X
- **외부 사이트 robots.txt·ToS**: LLM provider 가 책임. backend 직접 접근 없음
- **Document.source_id NOT NULL + RESTRICT FK**. sentinel `cold_start_pseudo` + `llm_search` 만 시드. 모든 Document 는 `llm_search` source_id
- **수집 실패가 다른 사용자에게 영향 X**: 사용자 격리 보장
- **NFR-25 정합**: LLM prompt 의 self-summary instruction 으로 Document.summary 채움. 외부 abstract 직접 복제 X
- **Clickbait default 비활성**: 사용자 명시 활성화 시만

## 검증

```bash
docker compose up -d
make migrate           # A2/A3 + alembic 0003 (Document/DocumentTopic/CollectionJob/ClickbaitResult + llm_search sentinel)
make import-cso        # A3 완료 가정

# manual run-now 트리거 (LLM_PROVIDER=mock fixture)
TOKEN=$(curl -s -X POST localhost:8000/auth/login ... | jq -r .access_token)
curl -X POST http://localhost:8000/collection/jobs/me/run-now \
  -H "Authorization: Bearer $TOKEN"

# Worker 진행 확인
docker compose logs worker --tail 50

# Document INSERT 확인
curl http://localhost:8000/collection/jobs/me \
  -H "Authorization: Bearer $TOKEN" | jq

# topic-level documents
curl http://localhost:8000/topics/<cso_topic_id>/documents \
  -H "Authorization: Bearer $TOKEN" | jq

# 정적 검증
mypy --strict backend/app/collection backend/app/llm_provider backend/app/topic
ruff check backend/
pytest backend/tests/collection backend/tests/llm_provider -v
make ci-regression-net
```

## 테스트 (FAIL-TO-PASS)

- `LLMProvider.search_with_tools` mock fixture 동작 (5 결과 응답 → SearchResult 5건)
- Dedup: 동일 DOI 2회 → 1행, 동일 canonical_url → 1행, utm_* 변형 URL → 1행, title Levenshtein ≥ 0.90 → 1행
- Orchestrator: active trace 0 → CollectionJob.status=SKIPPED. LLM raise → status=FAILED + failure_reason
- run-now rate limit 1/h
- /topics/{id}/documents cursor pagination
- Jitter deterministic (같은 user/day → 같은 sleep)
- NFR-25: prompt 에 self-summary instruction 포함

## 출력 형식

기본 + 추가:
- LLM provider 별 mock fixture 캡처 결과 (학술 / 빅테크 / 뉴스 도메인 응답 1건씩)
- Dedup 룰 단위 테스트 통과 결과
- Document INSERT 통합 검증 (1 user × 3 leaf → 30 결과 → dedup 후 N Document)
- /topics/{id}/documents 응답 sample
- 다음 Phase A5(News 활성화 시 후속) / A6(베이지안) / A7(leaf 재배치) / A8(추천) 가 봐야 할 사항
