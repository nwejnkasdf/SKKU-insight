# 에이전트 Kickoff Prompts

본 디렉토리는 **새 Claude/Codex 세션을 켤 때마다 복붙해서 사용**할 모듈별 kickoff prompt를 보관한다. 사용자가 본 디렉토리의 해당 파일을 새 세션의 첫 메시지로 그대로 붙여 넣으면 에이전트가 자기 모듈을 일관 작업한다.

## 사용 절차

1. 새 세션을 연다 (Claude Code, Codex CLI/Web, Cursor 등 자유)
2. 작업 디렉토리는 **항상** ``
3. 이 표에서 다음 작업 할 모듈을 고른다 (Phase 순서)
4. 해당 prompt 파일 본문을 그대로 새 세션 첫 메시지로 붙여 넣는다
5. 에이전트 작업 후 결과(commit hash 또는 diff 요약)를 본 표 "마지막 결과" 칸에 기록
6. CI 통과 여부 확인 (`scripts/check_*.py` 6종 + `mypy --strict` + `ruff` + `tsc --strict`)

## 진행 트래커

| Phase | 모듈 | Prompt 파일 | 상태 | 마지막 결과 (commit / 노트) |
|---|---|---|---|---|
| 0a | A2-stub | [`00-A2-stub.md`](00-A2-stub.md) | 🟢 | `73f57e8` (PR #4 머지 2026-05-11) — backend/ 33 파일 + contracts.py SOR (13 enum + 39 ErrorCode + 14 RedisKey) + 53 endpoint stubs + docs drift 24건 fix |
| 0b | A2 backend-foundation | [`01-A2-backend.md`](01-A2-backend.md) | ⬜ | |
| 0b | A3 cso-topic | [`02-A3-cso-topic.md`](02-A3-cso-topic.md) | ⬜ | |
| 1 | A4 collection | [`03-A4-collection.md`](03-A4-collection.md) | ⬜ | |
| 1 | A5 clickbait | [`04-A5-clickbait.md`](04-A5-clickbait.md) | 🟢 외부 서비스 완료 / 🟡 backend 통합 대기 | clickbait_module/ + vLLM AsyncLLMEngine 작성 완료 (commit 4066b20). backend `app/clickbait_client/`는 A2+A4 완료 후 |
| 1 | A6 interest-bayesian | [`05-A6-interest-bayesian.md`](05-A6-interest-bayesian.md) | ⬜ | |
| 2 | A7 leaf-lifecycle + traversal | [`06-A7-leaf-traversal.md`](06-A7-leaf-traversal.md) | ⬜ | |
| 2 | A8 recommendation | [`07-A8-recommendation.md`](07-A8-recommendation.md) | ⬜ | |
| 3 | A9 electron-client | [`08-A9-electron-client.md`](08-A9-electron-client.md) | ⬜ | |
| 3 | A10 admin-console | [`09-A10-admin-console.md`](09-A10-admin-console.md) | ⬜ | |
| 4 | A11 test-ci | [`10-A11-test-ci.md`](10-A11-test-ci.md) | ⬜ | |
| 4 | A12 demo-seed | [`11-A12-demo-seed.md`](11-A12-demo-seed.md) | ⬜ | |

상태 기호: ⬜ 대기 / 🟡 부분·블로커 / 🟢 완료 / 🔴 실패·재실행

## 의존 그래프

```
A1 ──(완료, 본 docs/)──
00 A2-stub ──> 모든 후속 (contracts.py + OpenAPI export)
01 A2 ──> A4, A5, A6, A9, A10
02 A3 ──> A4, A6, A7, A8
03 A4 ──> A5, A6, A7, A8
04 A5 ──> A4, A8
05 A6 ──> A7, A8
06 A7 ──> A8
07 A8 ──> A9
01 A2 + 07 A8 ──> A10
all ──> A11, A12
```

## 공통 prelude

모든 prompt 본문 상단에는 [`_common-disambiguation.md`](_common-disambiguation.md)의 핵심 룰을 자동 참조한다. 에이전트는 이 파일과 자기 모듈 prompt 둘 다 읽어야 한다.

## Code Review 흐름 (Claude 메인 + Codex 검토)

각 모듈 PR은 다음 절차로 검토:

1. Claude Code (메인) — `prompts/XX-YY.md` 본문을 새 세션에 복붙 → 모듈 구현 → PR 생성
2. Codex 플러그인 (검토자) — PR diff에 [`_review-prompt.md`](_review-prompt.md) 적용 → 4 카테고리(🔴 Critical / 🟡 Suggested / 🔵 Discussion / ⚪ Acknowledged) 분류 결과 받음
3. 사용자가 🔴 Critical만 보고 즉시 수정 시킴 (Claude Code에 같은 세션 또는 새 세션). 🟡 시간되면 반영, 🔵·⚪ 무시
4. CI 통과 → merge → 본 README 트래커 갱신

**검토자가 표준 패턴 회귀 권고를 내지 않도록 [`_review-prompt.md §2 False-positive 목록`](_review-prompt.md)에 비표준 결정이 박혀 있다**. 검토 결과의 ⚪ Acknowledged 항목은 무시.

## Phase별 사용자 검수 체크포인트

| Phase 종료 후 | 사용자 검수 항목 (시간) |
|---|---|
| 0a (A2-stub) | OpenAPI YAML과 docs/api/*.md 일치 + client·admin codegen 1회 (30분) |
| 0b (A2 + A3) | DB 스키마 ↔ alembic migration ↔ docs/data/schema.md 일치 + 인증 흐름 직접 호출 (30분) |
| 1 (A4 + A5 + A6) | arXiv 1일치 fetch 결과 + 베이지안 단순 시뮬레이션 + DoRA 통합 (60분) |
| 2 (A7 + A8) | 시드 페르소나로 cold-start → 첫 대시보드 + trace 생성 검증 (90분) |
| 3 (A9 + A10) | Electron 6 화면 + 시연 리허설 1회 (60분) |
| 4 (A11 + A12) | AT 자동화 결과 + 발표 자료 (60분) |

총 ~5.5시간 검수.

## 변경

prompt를 수정해야 할 사유가 생기면 (예: 새 결정, docs 갱신) 본 디렉토리의 해당 prompt도 같이 갱신. 단 작업 중인 모듈은 동일 prompt 유지.
