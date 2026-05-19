import { contextBridge, ipcRenderer } from "electron";

type StoredTokens = {
  accessToken: string;
  refreshToken: string;
};

contextBridge.exposeInMainWorld("insightAuth", {
  getTokens: (): Promise<StoredTokens | null> => ipcRenderer.invoke("auth:getTokens"),
  setTokens: (tokens: StoredTokens): Promise<void> => ipcRenderer.invoke("auth:setTokens", tokens),
  clearTokens: (): Promise<void> => ipcRenderer.invoke("auth:clearTokens")
});

contextBridge.exposeInMainWorld("insightEnv", {
  getApiBase: (): Promise<string> => ipcRenderer.invoke("env:getApiBase"),
  isEncryptionAvailable: (): Promise<boolean> => ipcRenderer.invoke("env:isEncryptionAvailable")
});

contextBridge.exposeInMainWorld("insightShell", {
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke("shell:openExternal", url)
});
