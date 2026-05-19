import type { ApiTokenStore, TokenPair } from "../generated/api";

type MemoryTokens = {
  accessToken: string;
  refreshToken: string;
} | null;

let memoryTokens: MemoryTokens = null;

export const tokenStore: ApiTokenStore = {
  async getAccessToken() {
    const tokens = await readTokens();
    return tokens?.accessToken ?? null;
  },
  async getRefreshToken() {
    const tokens = await readTokens();
    return tokens?.refreshToken ?? null;
  },
  async setTokens(tokens: TokenPair) {
    const normalized = {
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token
    };
    memoryTokens = normalized;
    if (window.insightAuth) {
      await window.insightAuth.setTokens(normalized);
    } else {
      localStorage.setItem("insight.tokens", JSON.stringify(normalized));
    }
  },
  async clearTokens() {
    memoryTokens = null;
    if (window.insightAuth) {
      await window.insightAuth.clearTokens();
    }
    localStorage.removeItem("insight.tokens");
  }
};

async function readTokens(): Promise<MemoryTokens> {
  if (memoryTokens) {
    return memoryTokens;
  }
  if (window.insightAuth) {
    memoryTokens = await window.insightAuth.getTokens();
    return memoryTokens;
  }
  const raw = localStorage.getItem("insight.tokens");
  if (!raw) {
    return null;
  }
  memoryTokens = JSON.parse(raw) as Exclude<MemoryTokens, null>;
  return memoryTokens;
}
