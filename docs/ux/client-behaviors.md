# Electron 클라이언트 동작 명세

본 파일은 Electron 클라이언트(`./client`)가 백엔드와 정합하게 동작하기 위해 지켜야 할 행동 룰을 정리한다. A9 에이전트가 클라이언트 코드 작성 시 본 표를 따른다. 토큰 보관은 [`../security/auth-flow.md`](../security/auth-flow.md), [`../security/token-handling.md`](../security/token-handling.md), 화면 상태는 [`wireframes.md`](wireframes.md), i18n은 [`i18n.md`](i18n.md).

## 1. Page Visibility 기반 dwell_tick 제어

dwell_tick은 사용자가 카드를 실제로 보는 시간을 측정한다. **백그라운드 시 자동 누적되면 베이지안 over-influence**.

```typescript
// renderer/src/dwell-tracker.ts
const TICK_INTERVAL_MS = 30_000;
let activeDocumentId: string | null = null;
let tickHandle: number | null = null;

function startTick(documentId: string) {
  stopTick();
  activeDocumentId = documentId;
  if (document.hidden) return;     // 백그라운드 시 시작 안 함
  tickHandle = window.setInterval(emitDwellTick, TICK_INTERVAL_MS);
}

function stopTick() {
  if (tickHandle != null) {
    window.clearInterval(tickHandle);
    tickHandle = null;
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopTick();
  else if (activeDocumentId) startTick(activeDocumentId);
});

window.addEventListener("blur", stopTick);    // Electron 창 비활성
window.addEventListener("focus", () => activeDocumentId && startTick(activeDocumentId));
```

baseline: `document.hidden === true` 또는 창 blur 상태에서는 tick 중단. 백엔드의 dwell_tick cap(문서당 4회)과 결합되어 over-influence 방어.

## 2. 토큰 보관 (`safeStorage`)

[`../security/token-handling.md §Electron 클라이언트 보관`](../security/token-handling.md) 참고. 핵심:

- main process에서 `safeStorage.encryptString(token)` 으로 암호화 후 `electron-store`에 저장
- renderer process는 IPC로 main에 토큰 요청 (renderer에 평문 토큰 보관 금지)
- 앱 시작 시 `safeStorage.decryptString()` 로 복호화 → 메모리만
- `safeStorage.isEncryptionAvailable()` false 시 (Linux backend 부재 등) 사용자에게 경고 + 매 실행마다 재로그인 요구

## 3. Cold-start 폴링

`POST /onboarding/interests` 응답 (202 + polling_url) 후 폴링.

```typescript
async function pollColdStart(pollingUrl: string): Promise<void> {
  const startedAt = Date.now();
  while (true) {
    if (Date.now() - startedAt > 60_000) {
      showError("cold_start.timeout");        // 60초 타임아웃
      return;
    }
    const status = await fetch(pollingUrl).then(r => r.json());
    if (status.status === "completed" && status.dashboard_ready) {
      navigate("/dashboard");
      return;
    }
    if (status.status === "failed") {
      showError(status.error_code);
      navigate("/dashboard");                  // fallback dashboard 표시
      return;
    }
    await sleep(1000);                          // 1초 간격
  }
}
```

폴링 timeout은 fetch timeout과 분리 (fetch는 5초, 폴링 루프는 60초).

## 4. 추천 캐시 invalidation 시 클라이언트 갱신

백엔드는 save/hide/not_interested 시 `recommendation:{user_id}` 캐시 폐기 ([`../api/recommendation.md`](../api/recommendation.md)). 클라이언트는 다음 dashboard 진입 시 자동 새로고침.

- 사용자 명시 액션(save/hide/not_interested) 후 즉시 카드 UI에서 해당 항목 숨김 (낙관 UI)
- 단순 click·dwell은 캐시 유지되므로 클라이언트도 재요청 안 함
- 사용자가 명시 refresh 버튼 누르면 `POST /recommendations/dashboard/refresh` 호출

## 5. 동의 철회 후 분기

`/consent/revoke` 호출 또는 다른 endpoint에서 `event.consent_required` (403) 받으면:

1. 메모리 상태 `consentActive = false` 설정
2. 모든 personalization 화면 차단
3. UI-05 변형 (재동의/계정삭제) 화면으로 강제 navigate
4. 토큰은 유지 (사용자가 재동의하면 즉시 복원 가능)

## 6. Offline·네트워크 단절

- 네트워크 단절 감지 (`navigator.onLine` + fetch 실패 카운터):
  - 캐시된 추천 + "오프라인" 배너 표시
  - 신규 인터랙션은 IndexedDB queue에 누적, 재연결 시 batch flush (서버의 `client_request_id` idempotency로 중복 방지)
- JWT 만료 응답(401 `auth.token_expired`) 시:
  - `/auth/refresh` 자동 호출
  - 실패하면 로그인 화면

**(C-55, 2026-05-24) 본 §의 구현 코드** ([`decisions.md §18`](../decisions.md)):
- [`client/src/lib/offlineQueue.ts`](../../client/src/lib/offlineQueue.ts) — `enqueue` / `flush` / `startAutoFlush` (window `online` event + 30s polling) / TTL 7d + max 100 row pruning
- [`client/src/lib/api.ts`](../../client/src/lib/api.ts) — InsightApi Proxy wrapper. mutation 4종 (postEvent / saveDocument / hideDocument / notInterestedDocument) 분기. network error 시 큐 enqueue + fake success (UI 낙관 갱신). 4xx (409 EVENT_DUPLICATE / 403 consent_required 포함) = 영구 실패 drop, 5xx = 큐 enqueue + throw
- `OfflineBanner` 컴포넌트 (`App.tsx`) — `navigator.onLine === false` 시만 표시. 큐 잔량 / internals 숨김 (narrative 정책 정합 — 추천 메커니즘 internals 사용자 노출 X). 본 banner 외 큐 동작은 silent

## 7. JWT refresh 자동화

```typescript
// fetch wrapper
async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  let token = await ipc.getAccessToken();
  let res = await fetch(url, { ...init, headers: { ...init.headers, Authorization: `Bearer ${token}` } });
  if (res.status === 401) {
    const errorCode = await res.clone().json().then(e => e.code).catch(() => null);
    if (errorCode === "auth.token_expired") {
      const newToken = await refreshTokens();
      if (newToken) {
        token = newToken;
        res = await fetch(url, { ...init, headers: { ...init.headers, Authorization: `Bearer ${token}` } });
      } else {
        navigate("/login");
      }
    }
  }
  return res;
}
```

## 8. 한국어 i18n

`react-i18next` 사용. UI 라벨은 한국어 단일 ([`i18n.md`](i18n.md)). 단 콘텐츠 (논문 제목, 영어 원문)는 그대로 노출하되 `reason_short`(추천 이유)와 `summary`(요약)는 한국어. 자세히는 i18n.md.

## 9. 점수·낚시성 노출 금지 (NFR-04, FR-32)

- API 응답에서 점수/낚시성 결과를 받지 않음 (백엔드가 마스킹)
- 만약 디버그 빌드에서 노출이 필요하면 `process.env.NODE_ENV === "development"` 분기로만, 프로덕션 빌드 시 dead code elimination

## 10. Rate limit·재시도

- 클라이언트 측 재시도 X (서버 429 응답 시 사용자에게 안내만)
- 단 네트워크 transient 오류(connection reset 등)는 1회 재시도

## 11. 부하 측면 클라이언트 책임

- dwell_tick은 30초 간격 (concurrency 부하 완화)
- 카드 viewport 진입 시 view 이벤트 1회만 (impression dedup)
- 폴링은 1초 간격 + 60초 타임아웃 (DDoS 방어)

## 12. 시연 모드 가속

`window.__INSIGHT_DEMO_MODE__ === true` (env에서 주입) 시:

- dwell_tick 5초 간격 (시연 빠른 진행)
- 폴링 0.5초 간격
- 디버그 panel (개발 도구 외 추가) 노출

A9는 위 12개 룰을 client 코드와 README에 반영.
