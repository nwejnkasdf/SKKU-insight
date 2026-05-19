import { app, BrowserWindow, ipcMain, safeStorage, shell } from "electron";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

type StoredTokens = {
  accessToken: string;
  refreshToken: string;
};

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function tokenPath(): string {
  return path.join(app.getPath("userData"), "tokens.bin");
}

async function readTokens(): Promise<StoredTokens | null> {
  if (!safeStorage.isEncryptionAvailable()) {
    return null;
  }
  try {
    const encrypted = await readFile(tokenPath());
    return JSON.parse(safeStorage.decryptString(encrypted)) as StoredTokens;
  } catch {
    return null;
  }
}

async function writeTokens(tokens: StoredTokens): Promise<void> {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("safeStorage encryption is not available.");
  }
  await mkdir(app.getPath("userData"), { recursive: true });
  await writeFile(tokenPath(), safeStorage.encryptString(JSON.stringify(tokens)));
}

async function clearTokens(): Promise<void> {
  await rm(tokenPath(), { force: true });
}

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1220,
    height: 820,
    minWidth: 980,
    minHeight: 680,
    title: "SKKU InSight",
    backgroundColor: "#f7f6f1",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  if (app.isPackaged) {
    win.loadFile(path.join(__dirname, "../../dist-renderer/index.html"));
  } else {
    win.loadURL("http://127.0.0.1:5173");
  }
}

ipcMain.handle("auth:getTokens", readTokens);
ipcMain.handle("auth:setTokens", async (_event, tokens: StoredTokens) => writeTokens(tokens));
ipcMain.handle("auth:clearTokens", clearTokens);
ipcMain.handle("env:getApiBase", () => process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000");
ipcMain.handle("env:isEncryptionAvailable", () => safeStorage.isEncryptionAvailable());
ipcMain.handle("shell:openExternal", async (_event, url: string) => shell.openExternal(url));

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
