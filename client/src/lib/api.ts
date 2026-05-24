import { ApiError, InsightApi } from "../generated/api";
import { MockInsightApi } from "./mockApi";
import {
  enqueue,
  notifyStatus,
  QueuedMutation,
  startAutoFlush,
} from "./offlineQueue";
import { tokenStore } from "./tokenStore";

let apiPromise: Promise<InsightApi> | null = null;

export function getApi(): Promise<InsightApi> {
  apiPromise ??= resolveApi();
  return apiPromise;
}

async function resolveApi(): Promise<InsightApi> {
  if (import.meta.env.VITE_USE_MOCK_API === "true") {
    return new MockInsightApi() as unknown as InsightApi;
  }
  const baseUrl =
    (await window.insightEnv?.getApiBase()) ||
    import.meta.env.VITE_API_BASE_URL ||
    "http://127.0.0.1:8000";
  const raw = new InsightApi(baseUrl, tokenStore);
  return wrapOfflineQueue(raw);
}

export { tokenStore };

/**
 * (C-55, 2026-05-24) InsightApi 의 mutation 4종을 IndexedDB queue 로 wrap.
 *
 * client-behaviors.md §6: 네트워크 단절 시 mutation 을 IndexedDB 에 누적, 재연결
 * 시 batch flush. duplicate 방지는 서버의 `client_request_id` idempotency (각
 * payload 에 이미 포함됨, 미포함 시 본 wrapper 가 자동 생성).
 *
 * scope (사용자 결정):
 * - 큐잉: postEvent / saveDocument / hideDocument / notInterestedDocument
 * - 제외: 조회 (GET), signup/login/consent (offline 진입 불가능)
 *
 * 실패 분기:
 * - network error (fetch reject) = 큐 enqueue + fake success 반환 (UI 낙관 갱신)
 * - 4xx (409 EVENT_DUPLICATE / 403 consent_required 포함) = 큐 진입 X, 즉시 throw
 * - 5xx = 큐 enqueue + throw (UI 가 에러 토스트, 큐가 재시도)
 */
function wrapOfflineQueue(raw: InsightApi): InsightApi {
  const dispatcher = async (
    mutation: QueuedMutation
  ): Promise<{ done: boolean }> => {
    try {
      switch (mutation.kind) {
        case "postEvent":
          await raw.postEvent(
            mutation.payload as Parameters<InsightApi["postEvent"]>[0]
          );
          return { done: true };
        case "saveDocument":
          await raw.saveDocument(mutation.payload as string);
          return { done: true };
        case "hideDocument":
          await raw.hideDocument(mutation.payload as string);
          return { done: true };
        case "notInterestedDocument":
          await raw.notInterestedDocument(mutation.payload as string);
          return { done: true };
      }
    } catch (err) {
      // 4xx (409 EVENT_DUPLICATE / 403 consent_required 포함) = 영구 실패 drop.
      // 5xx + network = 보존 (다음 flush 재시도) → done=false.
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        return { done: true };
      }
      return { done: false };
    }
    return { done: false };
  };
  startAutoFlush(dispatcher);

  const queueOrThrow = async <T>(
    kind: QueuedMutation["kind"],
    payload: unknown,
    direct: () => Promise<T>
  ): Promise<T> => {
    try {
      const result = await direct();
      void notifyStatus();
      return result;
    } catch (err) {
      // network error (TypeError "Failed to fetch") = 큐 enqueue + fake success.
      const isNetworkError = !(err instanceof ApiError);
      if (isNetworkError) {
        await enqueue({ kind, payload, created_at: Date.now() });
        void notifyStatus();
        // 낙관 갱신 — caller 는 UI 즉시 반영. 실제 서버 적용은 다음 flush 때.
        return undefined as T;
      }
      // 4xx/5xx = re-throw (caller 가 토스트 / error UI).
      // 5xx 도 큐에 enqueue (재시도) — caller 는 에러 인지 + 큐가 처리.
      if (err instanceof ApiError && err.status >= 500) {
        await enqueue({ kind, payload, created_at: Date.now() });
        void notifyStatus();
      }
      throw err;
    }
  };

  // Proxy 로 mutation 4종만 wrap, 나머지는 그대로 위임.
  return new Proxy(raw, {
    get(target, prop, receiver) {
      if (prop === "postEvent") {
        return (payload: Parameters<InsightApi["postEvent"]>[0]) =>
          queueOrThrow("postEvent", payload, () => target.postEvent(payload));
      }
      if (prop === "saveDocument") {
        return (documentId: string) =>
          queueOrThrow("saveDocument", documentId, () =>
            target.saveDocument(documentId)
          );
      }
      if (prop === "hideDocument") {
        return (documentId: string) =>
          queueOrThrow("hideDocument", documentId, () =>
            target.hideDocument(documentId)
          );
      }
      if (prop === "notInterestedDocument") {
        return (documentId: string) =>
          queueOrThrow("notInterestedDocument", documentId, () =>
            target.notInterestedDocument(documentId)
          );
      }
      return Reflect.get(target, prop, receiver);
    },
  });
}
