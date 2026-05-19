import { InsightApi } from "../generated/api";
import { MockInsightApi } from "./mockApi";
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
  return new InsightApi(baseUrl, tokenStore);
}

export { tokenStore };
