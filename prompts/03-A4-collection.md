# A4 — Collection (Phase 1)

> 작업 디렉토리: ``
> **사전조건**: A2 backend + A3 cso-topic 완료. 단 A5(clickbait)·A6(베이지안) 진행과 병렬 가능.

## 너의 역할

사용자별 일일 수집 잡 + 6 source 어댑터 + Document/CollectionJob 영속 + jitter 스케줄러. 결과를 A5 clickbait 필터로 넘김.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `docs/data/sources-registry.md` (전체 — 학술 4 + 빅테크 30+ + 뉴스)
- `docs/data/schema.md` (Document, DocumentTopic, CollectionJob, Source, SourcePolicy, ClickbaitResult, TopicLinkageError)
- `docs/api/collection.md`
- `docs/sdd/data-flow.md` §2 (수집 시퀀스)
- `docs/sdd/concurrency.md` §8 (jitter)
- `docs/algorithms/clickbait-integration.md` (호출 인터페이스)
- `docs/algorithms/cso-topic-traversal.md` §6.1 (수집 대상 = active trace path ∪ 1-hop adjacent — A7 의존, 본 세션은 단순 fallback)
- `docs/ops/runbooks.md` §1 (수집 실패 대응)

## 산출

### 1. SourceAdapter 추상 + 6 구현체
- `backend/app/source_adapters/base.py` — `SourceAdapter` Protocol (module-boundaries.md)
- `arxiv.py` (cs.* 카테고리, OAI-PMH or `export.arxiv.org/api/query`)
- `openalex.py` (`api.openalex.org/works`, polite email)
- `semantic_scholar.py` (TLDR 제공)
- `dblp.py` (서지 메타)
- `rss.py` (generic, feedparser)
- `naver_bs4.py` (BeautifulSoup, 네이버뉴스 IT/과학)

각 어댑터는:
- `fetch(topic_query, since, max_items=100) -> list[RawDocument]`
- httpx + retry (exponential backoff) + rate_limit_per_minute 준수
- 외부 응답 변동·실패 시 `SourceFetchError` raise → CollectionJob.failure_reason 기록

### 2. sources.yaml 시드 + Source 테이블 동기화
- `backend/sources.yaml` — `data/sources-registry.md` 골격 그대로 (38+ 항목)
- `scripts/seed_sources.py` 또는 alembic data migration
- **`# TODO: verify URL` 마커가 있는 항목은 본 세션에 부트 시 GET 호출로 검증** + 200 OK 또는 `<rss>`/`<feed>` 응답 시 마커 제거. 실패 시 `enabled=false` 자동.

### 3. CollectionOrchestrator
- `backend/app/collection/orchestrator.py`
- 사용자별 수집 대상 = active UserCSOTraversal path 노드 ∪ 1-hop adjacent (A7 데이터 의존). **A7 미완료 시 fallback**: User onboarding 선택 cluster의 1-hop adjacent
- 6 어댑터 dispatch (per_user_parallel=4, global_concurrency=8)
- 결과 dedup (URL/canonical_url/DOI/제목 정규화 + Levenshtein)
- 각 Document INSERT, DocumentTopic 매핑 (A3 그래프 활용 + LLM 호출 1회/Document로 leaf 매핑은 A7 의존)
- tech_news content_type → A5 ClickbaitClassifier 호출

### 4. RQ scheduler + jitter
- `COLLECTION_CRON=0 3 * * *` 발화 → 각 사용자에 0~5분 jitter (concurrency.md §8)
- `backend/workers/collection_worker.py` — RQ worker entrypoint

### 5. Endpoint 본문
- `GET /collection/jobs/me` (자기 잡)
- `POST /collection/jobs/me/run-now` (1/시간/사용자, 시연용)
- `/admin/collection/*` (admin 본문은 A10이 조회 UI 담당, 본 세션은 backend endpoint만)

### 6. 운영 가드
- 외부 사이트 robots.txt·ToS 점검 — 네이버 BS4는 metadata 중심, 원문 본문 미저장 (NFR-25)
- DocumentTopic.confidence 단발 INSERT (재평가 룰 P2-2)
- 네이버뉴스 야간 정리 잡 (decision-backlog P1-6, default 매일 02:00 KST cron + 30일 미매핑 → 삭제)

## 헌법 (재강조)

- **임베딩 미사용**. 토픽 매핑은 A3 그래프 + LLM 호출 (제목·abstract → cso_topic_id). LLM은 medium slot.
- **외부 사이트 변동 시 stub 응답 fallback**: 실패하면 `clickbait_classifier_unavailable` 패턴처럼 `source_unavailable` 으로 기록 + 추천 제외.
- **Document.source_id NOT NULL + RESTRICT FK**. sentinel cold_start_pseudo는 cold-start 전용, 본 세션이 만드는 Document는 모두 enabled Source 매핑.
- **수집 실패가 다른 사용자에게 영향 X**: 사용자 격리 보장.

## 검증

```bash
docker compose up -d
make import-cso        # A3 완료 가정
make seed-sources      # sources.yaml → DB
docker compose exec api python -m app.collection.orchestrator --user-id <test_user> --since 24h
docker compose logs worker --tail 50

# arXiv 1일치 fetch 확인
curl http://localhost:8000/collection/jobs/me -H "Authorization: Bearer $TOKEN"

mypy --strict backend/app/collection backend/app/source_adapters
ruff check backend/
pytest backend/tests/collection backend/tests/source_adapters -v
```

테스트:
- 어댑터 6종 각각 1개 fixture 응답 → RawDocument 변환
- dedup (같은 DOI 두 번 → 1 row)
- jitter 분포 검증 (사용자 20명 → 5분 윈도우)
- run-now rate limit 1/시간/사용자
- robots.txt·ToS 준수 (네이버 BS4 fixture 응답에서 metadata만 추출)

## 출력 형식

기본 + 추가:
- 어댑터 6종 각각 fixture 응답 검증 결과
- sources.yaml verify URL 통과/실패 갯수
- 시드 후 Source 테이블 행 수 (~50)
- Document·DocumentTopic·CollectionJob INSERT 통합 검증
- 다음 Phase A5/A6/A7/A8가 봐야 할 사항
