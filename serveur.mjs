// Petit serveur local pour utiliser Cahier Malin sur cet ordinateur : node serveur.mjs
import http from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(new URL(".", import.meta.url)), "app");
const port = Number(process.env.PORT) || 5173;
const types = { ".html": "text/html; charset=utf-8", ".mjs": "text/javascript", ".js": "text/javascript", ".webmanifest": "application/manifest+json", ".png": "image/png", ".json": "application/json" };

http.createServer(async (req, res) => {
  let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (p.endsWith("/")) p += "index.html";
  const file = normalize(join(root, p));
  if (!file.startsWith(root)) { res.writeHead(403).end(); return; }
  try {
    const body = await readFile(file);
    res.writeHead(200, { "Content-Type": types[extname(file)] || "application/octet-stream", "Cache-Control": "no-cache" }).end(body);
  } catch { res.writeHead(404).end("Introuvable"); }
}).listen(port, "127.0.0.1", () => console.log(`Cahier Malin : http://localhost:${port}`));
