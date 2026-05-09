# A9 — Electron Client (Phase 3)

> 작업 디렉토리: `/Users/hyojung/학교 과제/소프트웨어공학개론/`
> **사전조건**: Phase 0a A2-stub의 OpenAPI export + client codegen 완료. A2·A8 완료 권장 (실제 endpoint 동작).

## 너의 역할

Windows 데스크톱 사용자 앱 (Electron + React + TypeScript). UI-01~05 6개 화면. **codegen된 `client/src/generated/api.ts`만 사용** (raw fetch 금지).

## 첫 5분 — 반드시 read

`prompts/_common-disambiguation.md` "첫 5분" + 다음:

- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/ux/wireframes.md` (전체)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/ux/ui-states.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/ux/i18n.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/ux/client-behaviors.md` (전체 12 룰)
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/api/auth.md`, `consent.md`, `onboarding.md`, `interest.md`, `topics.md`, `recommendation.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/security/auth-flow.md`, `token-handling.md`
- `/Users/hyojung/학교 과제/소프트웨어공학개론/docs/sdd/api-conventions.md`

## 산출

### 1. Electron + React + TypeScript 프로젝트
- `client/package.json` (Electron 30+, React 18, TS 5, Vite, react-i18next)
- `client/electron/main.ts` — main process, safeStorage, IPC
- `client/electron/preload.ts` — IPC bridge (toklen·DB cache)
- `client/src/` — renderer

### 2. 화면 6개 (UI-01~06 중 사용자용 5개)
- UI-01 onboarding (가입 → 동의 → 12 클러스터 선택)
- UI-02 dashboard (10 추천 카드 5/3/2 슬롯)
- UI-03 topic detail
- UI-04 document detail (4 섹션 요약)
- UI-05 settings & feedback (saved/hidden 목록, 동의 철회, 계정 삭제)

각 화면은 ux/wireframes.md 의 Mermaid 상태 머신 + 정상/빈/오류 매트릭스를 1:1로 구현.

### 3. codegen된 API client만 사용
- `client/src/generated/api.ts` (Phase 0a에서 codegen됨)
- `client/src/api/wrapper.ts` — 토큰 관리·401 refresh·429 retry 등 표준 wrapper
- raw fetch 금지

### 4. safeStorage 토큰 보관
- main process에서 `safeStorage.encryptString(token)` → electron-store
- IPC `getAccessToken` / `setTokens` / `clearTokens`
- renderer는 IPC만 호출 (renderer에 평문 토큰 X)

### 5. Page Visibility dwell_tick
- `client-behaviors.md §1` 그대로 — `document.hidden` 시 tick 중단
- 30초 간격, blur·focus 핸들링

### 6. Cold-start 폴링
- `client-behaviors.md §3` 그대로 — 1초 간격, 60초 timeout

### 7. 한국어 i18n
- react-i18next + `i18n.md` 룰
- locales/ko.json 작성 (모든 라벨)
- 콘텐츠는 한·영 병행 (논문 제목 영어 그대로, reason_short·summary는 한국어)

### 8. 동의 철회 UX
- 403 `event.consent_required` 받으면 즉시 메모리 `consentActive=false` + UI-05 변형 화면
- 토큰 유지 (재동의 시 즉시 복원)

### 9. Offline·재시도
- IndexedDB queue (client-behaviors.md §6)
- JWT auto refresh (client-behaviors.md §7)

### 10. 빌드·시연 모드
- `client-behaviors.md §12` 시연 가속 모드 (`window.__INSIGHT_DEMO_MODE__`)
- `npm run dev` (개발), `npm run build:win` (Windows installer, 1차는 미실행)

## 헌법 (재강조)

- **codegen된 api.ts만 사용**. raw fetch 금지. `npm run codegen` 으로 OpenAPI 변경 시 갱신.
- **점수·낚시성 노출 X** (NFR-04, FR-32). 디버그 빌드도 production build에 dead code elimination.
- **렌더러에 평문 토큰 X**. 모든 토큰 접근은 IPC.
- **fix UI 한국어**. 단 영어 원문(논문 제목)은 그대로.
- **`assets/wire_*.png` 없음**. wireframes.md의 Mermaid 상태머신 + 매트릭스가 단일 진실 공급원.

## 검증

```bash
cd client
npm install
npm run codegen        # OpenAPI 결과 import (Phase 0a commit과 일치)
npm run dev            # Electron 부트
# 가입 → 동의 → 클러스터 선택 → cold-start polling → 대시보드

npm run typecheck      # tsc --strict
npm run lint           # eslint
npm test               # vitest

# 시연 fixture (mock LLM)
LLM_PROVIDER=mock npm run dev
```

테스트:
- 각 화면별 정상/빈/오류 상태 (UI-01~05 × 3 = 15+ 테스트)
- safeStorage 토큰 round-trip
- Page Visibility dwell_tick on/off
- Cold-start polling 8초 timeout
- 동의 철회 후 personalization 차단 + UI-05 redirect
- JWT refresh 자동 + 실패 시 로그인

## 출력 형식

기본 + 추가:
- 6 화면 구현 완료 + 상태 머신 검증
- safeStorage round-trip 확인
- API codegen 활용 비율 (raw fetch 0건 확인)
- 시연 fixture로 end-to-end 1회 흐름 검증
- A10 admin-console가 codegen 결과 동일 패턴으로 사용해야 함을 명시
