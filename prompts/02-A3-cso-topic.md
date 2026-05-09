# A3 — CSO Topic Engine (Phase 0b)

> 작업 디렉토리: `/Users/hyojung/학교 과제/소프트웨어공학개론/`
> **사전조건**: Phase 0a A2-stub 완료. A2 본문과 병렬 가능 (DB schema의 CSOTopic 테이블만 공유, 본 세션이 시드).

## 너의 역할

CSO (Computer Science Ontology) 데이터를 PostgreSQL에 임포트하고 NetworkX 메모리 그래프 캐시를 구축. 토픽 탐색 API (`/topics/cso/*`, `/topics/leaves` 일부) 구현.

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` 의 "첫 5분" 5개 + 다음:

- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/algorithms/cso-mapping.md` (그래프 탐색 알고리즘 + 12 클러스터 매핑)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/data/cso-import.md` (다운로드·파싱·Alembic seed)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/data/schema.md` (CSOTopic, DynamicLeafTopic, DynamicLeafTopicCSOTopic 부분)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/topics.md` (모든 endpoint)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/algorithms/cso-topic-traversal.md` (§1.3 leaf 분기 영역 제약, §6.1 카테고리 정의 — A7·A8과 의존성)

## 산출

### 1. CSO 임포트 스크립트
- `scripts/import_cso.py` — `data/cso-import.md` 의사 코드 그대로
- 다운로드 캐시 `.cache/cso/CSO.3.4.csv`
- 12 cluster seed BFS 라벨링
- Alembic data migration 또는 직접 INSERT
- `make import-cso` Makefile target

### 2. NetworkX 캐시
- `backend/app/topic/graph.py` — 서비스 시작 시 build_cso_graph() (`docs/algorithms/cso-mapping.md`)
- `app.state.cso_graph` 에 register
- `find_adjacent`, `find_ancestors`, `find_descendants`, `find_equivalents`, `map_to_clusters`, `graph_distance` 함수 (cso-mapping.md 그대로)
- 검증: `verify_cso_import(g)` — 12 클러스터 모두 존재, DAG, 노드 수 ~14k

### 3. 토픽 endpoint 본문
- `GET /topics/cso/clusters` (12 클러스터, BroadInterest 시드 활용)
- `GET /topics/cso/{id}`, `GET /topics/cso/{id}/adjacent`, `GET /topics/cso/{id}/descendants`
- `GET /topics/leaves?status=active`, `GET /topics/leaves/{id}`, `GET /topics/{id}/documents` (PagedResponse)
- `GET /topics/traces?status=active`, `GET /topics/traces/{trace_id}` — A7이 작성하는 UserCSOTraversal 데이터 조회 (본 세션은 read-only, score_tail 마스킹)

### 4. 동의 활성 미들웨어 적용
- 모든 GET endpoint에 consent_active 검증
- 사용자별 격리 (DynamicLeafTopic.user_id, UserCSOTraversal.user_id JWT 클레임 필터)

## 헌법 (재강조)

- **contracts.py 외 enum 정의 금지** — LeafTopicStatus, TraversalStatus 등 모두 import.
- **다른 모듈 데이터 작성 금지**: 본 세션은 CSOTopic·BroadInterest 시드만. DynamicLeafTopic은 A7이, UserCSOTraversal은 A7이 작성. 본 세션은 read endpoint만.
- **CSO 14k 노드 임포트 시간 5분 이내 목표**. 초과 시 cso-import.md 의사 코드 검토 후 최적화.
- **CSO version pin**: 1차는 CSO 3.4. `CSO_DOWNLOAD_URL` env로 변경 가능 (decision-backlog P1-5).

## 검증

```bash
cd backend
make import-cso
docker compose restart api
curl http://localhost:8000/topics/cso/clusters | jq '.clusters | length'   # 12
curl "http://localhost:8000/topics/cso/{any_uuid}/adjacent?hops=1"
mypy --strict backend/app/topic
ruff check backend/app/topic
pytest backend/tests/topic -v
python scripts/check_api_docs.py
python scripts/check_schema.py
```

테스트:
- import_cso 단위 테스트 (작은 fixture CSO 그래프)
- find_adjacent / find_descendants 검증
- 12 클러스터 BFS 라벨링 검증
- API endpoint 200 응답 + PagedResponse envelope

## 출력 형식

기본 + 추가:

- CSO 노드 수, 엣지 수, cluster 12 매핑된 노드 수
- 12 클러스터 각각의 후손 카운트
- import 소요 시간
- API endpoint 갯수 (8개)
- 다음 Phase에 영향 줄 사항 (예: CSO 그래프 cycle 발견, schema 모순 등)
