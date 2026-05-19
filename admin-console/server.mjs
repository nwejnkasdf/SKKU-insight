import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("./public", import.meta.url));
const port = Number(process.env.PORT || 3000);
const apiBase = process.env.NEXT_PUBLIC_API_BASE || process.env.API_PUBLIC_BASE || "http://localhost:8000";

const mimeTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"]
]);

function resolvePublicPath(urlPath) {
  const cleanPath = normalize(decodeURIComponent(urlPath.split("?")[0])).replace(/^(\.\.[/\\])+/, "");
  const requested = cleanPath === "/" ? "/index.html" : cleanPath;
  return join(root, requested);
}

const server = createServer(async (req, res) => {
  try {
    const urlPath = req.url || "/";
    if (urlPath.startsWith("/config.js")) {
      res.writeHead(200, { "Content-Type": "text/javascript; charset=utf-8" });
      res.end(`window.__ADMIN_CONFIG__ = ${JSON.stringify({ apiBase })};\n`);
      return;
    }

    const filePath = resolvePublicPath(urlPath);
    const body = await readFile(filePath);
    res.writeHead(200, {
      "Content-Type": mimeTypes.get(extname(filePath)) || "application/octet-stream"
    });
    res.end(body);
  } catch {
    const body = await readFile(join(root, "index.html"));
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(body);
  }
});

server.listen(port, "0.0.0.0", () => {
  console.log(`admin-console listening on :${port} api=${apiBase}`);
});
