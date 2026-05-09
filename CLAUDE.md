# Claude Code agent guide — see AGENTS.md

본 저장소의 모든 에이전트(Claude·Codex·사람) 가이드는 단일 파일 [`AGENTS.md`](AGENTS.md)에 통합돼 있다. **본 파일은 redirect 한 장이며 여기서 읽기를 멈추지 말고 다음 두 파일을 직접 열어보라.**

1. [`AGENTS.md`](AGENTS.md) — 에이전트 진입점, 디렉토리 지도, 작업 규칙 14조, 5겹 방어, 에이전트 분할표 (A1·A2-stub·A2~A12).
2. [`docs/decisions.md`](docs/decisions.md) — 12+ 라운드 결정 매트릭스 (단일 진실 공급원).
3. [`docs/decision-backlog.md`](docs/decision-backlog.md) — P0/P1/P2 미해결 항목 + default/stub.
4. [`docs/sdd/contracts.md`](docs/sdd/contracts.md) + [`docs/sdd/agent-orchestration.md`](docs/sdd/agent-orchestration.md) — 멀티 에이전트 안전장치(contracts SOR + Phase 0a stub-only + 5겹 방어).
5. (사람용 프로젝트 소개) [`README.md`](README.md).

CLAUDE.md를 별도로 두는 이유: Claude Code 기본 인식 파일명이 `CLAUDE.md`이고, OpenAI Codex 등 다른 도구의 표준이 `AGENTS.md`이므로 두 표준을 모두 충족하기 위해 redirect 형태로 분리. 두 파일의 내용이 갈라지지 않도록 본 파일은 단순 포인터로만 유지한다.
