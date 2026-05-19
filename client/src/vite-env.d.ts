/// <reference types="vite/client" />

type InsightTokens = {
  accessToken: string;
  refreshToken: string;
};

interface Window {
  insightAuth?: {
    getTokens: () => Promise<InsightTokens | null>;
    setTokens: (tokens: InsightTokens) => Promise<void>;
    clearTokens: () => Promise<void>;
  };
  insightEnv?: {
    getApiBase: () => Promise<string>;
    isEncryptionAvailable: () => Promise<boolean>;
  };
  insightShell?: {
    openExternal: (url: string) => Promise<void>;
  };
  __INSIGHT_DEMO_MODE__?: boolean;
}
