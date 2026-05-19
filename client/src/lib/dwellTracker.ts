import type { InsightApi, UUID } from "../generated/api";

const NORMAL_TICK_MS = 30_000;
const DEMO_TICK_MS = 5_000;

let activeDocumentId: UUID | null = null;
let tickHandle: number | null = null;
let apiRef: InsightApi | null = null;

export function configureDwellTracker(api: InsightApi): void {
  apiRef = api;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopDwell();
    } else if (activeDocumentId) {
      startDwell(activeDocumentId);
    }
  });
  window.addEventListener("blur", stopDwell);
  window.addEventListener("focus", () => {
    if (activeDocumentId) {
      startDwell(activeDocumentId);
    }
  });
}

export function startDwell(documentId: UUID): void {
  stopDwell();
  activeDocumentId = documentId;
  if (document.hidden) {
    return;
  }
  tickHandle = window.setInterval(() => {
    void emitDwellTick(documentId);
  }, tickIntervalMs());
}

export function stopDwell(): void {
  if (tickHandle !== null) {
    window.clearInterval(tickHandle);
    tickHandle = null;
  }
}

async function emitDwellTick(documentId: UUID): Promise<void> {
  if (!apiRef) {
    return;
  }
  await apiRef
    .postEvent({
      event_type: "dwell_tick",
      document_id: documentId,
      dwell_ms: tickIntervalMs(),
      occurred_at: new Date().toISOString(),
      client_request_id: crypto.randomUUID()
    })
    .catch(() => undefined);
}

function tickIntervalMs(): number {
  return window.__INSIGHT_DEMO_MODE__ ? DEMO_TICK_MS : NORMAL_TICK_MS;
}
