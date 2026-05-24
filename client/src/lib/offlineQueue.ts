/**
 * C-55 (2026-05-24) IndexedDB offline queue.
 *
 * client-behaviors.md §6 본문 — 네트워크 단절 시 mutation 을 IndexedDB 에 누적,
 * 재연결 시 batch flush. duplicate 방지는 서버의 `client_request_id` idempotency.
 *
 * 디자인 결정 (사용자 결정, 4건):
 * 1. flush 방식 = 단건 POST loop (codegen 의 `/events` `/feedback/*` endpoint 그대로,
 *    `/events/batch` 미사용).
 * 2. flush 트리거 = window `online` event 즉시 + 30s polling 백업.
 * 3. UI 노출 = 작은 offline 배너 (큐 internals 숨김, narrative 정책 정합).
 * 4. queue scope = postEvent / saveDocument / hideDocument / notInterestedDocument
 *    (signup·login·consent 제외, 조회 GET 제외).
 *
 * 기타 디자인:
 * - failure 처리: 4xx (409 EVENT_DUPLICATE / 403 consent_required 포함) = drop,
 *   5xx + network error = retry. drop / retry 분기는 caller (proxy) 의 ApiError 분류.
 * - max 100 row (oldest first drop) + 7일 이상 row 자동 drop.
 * - DB name `insight_offline` / object store `pending_events` / autoIncrement key.
 */

const DB_NAME = "insight_offline";
const DB_VERSION = 1;
const STORE_NAME = "pending_events";
const MAX_ROWS = 100;
const ROW_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const FLUSH_POLL_INTERVAL_MS = 30 * 1000;

export type QueuedMutation = {
  /** mutation 종류 — proxy 가 분기 dispatch 시 사용. */
  kind: "postEvent" | "saveDocument" | "hideDocument" | "notInterestedDocument";
  /** kind 별로 다른 payload 모양. proxy 가 그대로 InsightApi 메서드 인자로 전달. */
  payload: unknown;
  /** enqueue 시점 epoch ms. TTL drop 기준. */
  created_at: number;
};

type QueuedRow = QueuedMutation & { id: number };

let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id", autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

async function withStore<T>(
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => Promise<T> | T
): Promise<T> {
  const db = await openDb();
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, mode);
    const store = tx.objectStore(STORE_NAME);
    let result: T | undefined;
    Promise.resolve(fn(store))
      .then((r) => {
        result = r;
      })
      .catch(reject);
    tx.oncomplete = () => resolve(result as T);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

function reqAsPromise<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function getAll(): Promise<QueuedRow[]> {
  return withStore("readonly", async (store) => {
    const rows = (await reqAsPromise(store.getAll())) as QueuedRow[];
    return rows.sort((a, b) => a.id - b.id);
  });
}

async function deleteRow(id: number): Promise<void> {
  await withStore("readwrite", (store) => {
    store.delete(id);
  });
}

async function pruneIfNeeded(): Promise<void> {
  const rows = await getAll();
  const now = Date.now();
  // (1) TTL drop — 7d 이상 row 제거.
  const expired = rows.filter((r) => now - r.created_at > ROW_TTL_MS);
  for (const r of expired) {
    await deleteRow(r.id);
  }
  // (2) max 100 — 가장 오래된 row 부터 drop.
  const remaining = rows.filter((r) => !expired.includes(r));
  if (remaining.length > MAX_ROWS) {
    const drop = remaining.slice(0, remaining.length - MAX_ROWS);
    for (const r of drop) {
      await deleteRow(r.id);
    }
  }
}

export async function enqueue(mutation: QueuedMutation): Promise<void> {
  await withStore("readwrite", (store) => {
    store.add(mutation);
  });
  // prune 는 enqueue 직후 best-effort (실패해도 add 자체는 성공).
  pruneIfNeeded().catch(() => {
    /* 큐 정리 실패 무시 — 다음 enqueue 시 재시도 */
  });
}

export async function queueSize(): Promise<number> {
  return withStore("readonly", async (store) => {
    return (await reqAsPromise(store.count())) as number;
  });
}

export type FlushDispatcher = (mutation: QueuedMutation) => Promise<{
  /** true = 큐에서 DELETE (성공 또는 영구 실패 drop). */
  done: boolean;
}>;

let flushInFlight = false;

/**
 * 큐 row 를 순차 dispatch. dispatcher 가 done=true 반환 시 row DELETE.
 * 동시 호출 차단 (flushInFlight flag).
 */
export async function flush(dispatcher: FlushDispatcher): Promise<{
  attempted: number;
  drained: number;
}> {
  if (flushInFlight) return { attempted: 0, drained: 0 };
  flushInFlight = true;
  let attempted = 0;
  let drained = 0;
  try {
    const rows = await getAll();
    for (const row of rows) {
      attempted += 1;
      try {
        const result = await dispatcher({
          kind: row.kind,
          payload: row.payload,
          created_at: row.created_at,
        });
        if (result.done) {
          await deleteRow(row.id);
          drained += 1;
        } else {
          // retry — 다음 flush 까지 보존, loop break (순서 보장).
          break;
        }
      } catch {
        // dispatcher 자체 예외 (네트워크 끊김 등) — 보존하고 loop break.
        break;
      }
    }
  } finally {
    flushInFlight = false;
  }
  return { attempted, drained };
}

let onlineListener: (() => void) | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;

/** 자동 flush 시작 — online event + 30s polling 둘 다 활성화. 단일 호출 권장. */
export function startAutoFlush(dispatcher: FlushDispatcher): void {
  stopAutoFlush();
  const trigger = (): void => {
    if (typeof navigator !== "undefined" && navigator.onLine === false) return;
    void flush(dispatcher);
  };
  onlineListener = trigger;
  if (typeof window !== "undefined") {
    window.addEventListener("online", onlineListener);
  }
  pollTimer = setInterval(trigger, FLUSH_POLL_INTERVAL_MS);
  // 즉시 1회 시도 — 새로고침 직후 대기 자료 빠르게 처리.
  trigger();
}

export function stopAutoFlush(): void {
  if (onlineListener && typeof window !== "undefined") {
    window.removeEventListener("online", onlineListener);
  }
  onlineListener = null;
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

/** 외부에서 isOnline 모니터링용 — `navigator.onLine` 단순 wrap. */
export function isOnline(): boolean {
  return typeof navigator === "undefined" ? true : navigator.onLine;
}

/** 큐 잔량 / online 상태 구독용 lightweight subscriber. */
export type OfflineQueueStatus = {
  online: boolean;
  pending: number;
};

const statusSubscribers = new Set<(s: OfflineQueueStatus) => void>();

export function subscribeStatus(
  callback: (s: OfflineQueueStatus) => void
): () => void {
  statusSubscribers.add(callback);
  void notifyStatus();
  return () => {
    statusSubscribers.delete(callback);
  };
}

export async function notifyStatus(): Promise<void> {
  const status: OfflineQueueStatus = {
    online: isOnline(),
    pending: await queueSize().catch(() => 0),
  };
  for (const cb of statusSubscribers) {
    cb(status);
  }
}

// online/offline 변화 시 자동 notify.
if (typeof window !== "undefined") {
  window.addEventListener("online", () => {
    void notifyStatus();
  });
  window.addEventListener("offline", () => {
    void notifyStatus();
  });
}
