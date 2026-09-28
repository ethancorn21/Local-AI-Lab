#!/usr/bin/env node
// game-shot: load a browser game headlessly, play a scripted input sequence, save screenshots, report console errors.
//
//   game-shot [--dir .] [--size 1280x720] [--actions '<json array>'] [--out .agent/shots/shot.png]
//
// Serves --dir over http on a random local port (no separate server needed) and opens index.html.
// Actions run in order; screenshots are taken wherever {"shot": "name.png"} appears, plus --out at the end.
//   {"wait": 500}                      wait ms
//   {"key": "KeyW", "hold": 800}       hold a key (KeyboardEvent.code) for ms
//   {"press": "KeyI"}                  tap a key
//   {"click": [640, 360]}              left click at canvas/page pixel
//   {"move": [900, 200]}               move the mouse (aiming)
//   {"shot": "after-move.png"}         screenshot now (saved next to --out)
//   {"eval": "window.__game?.state.player.hp"}   evaluate JS in the page and print the result
// Then view the PNG with your read tool.
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { extname, join, resolve, dirname } from "node:path";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(join(process.env.HOME, "tools/shot/node_modules/playwright"));

const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, arr) => (v.startsWith("--") ? [...a, [v.slice(2), arr[i + 1]]] : a), []));
const dir = resolve(args.dir || ".");
const [w, h] = (args.size || "1280x720").split("x").map(Number);
const out = resolve(args.out || ".agent/shots/shot.png");
const actions = JSON.parse(args.actions || '[{"wait": 1500}]');
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml", ".wav": "audio/wav", ".ogg": "audio/ogg" };

const server = createServer(async (req, res) => {
  const p = join(dir, decodeURIComponent(new URL(req.url, "http://x").pathname).replace(/\/$/, "/index.html"));
  if (!p.startsWith(dir)) { res.writeHead(403).end(); return; }
  try { const body = await readFile(p); res.writeHead(200, { "content-type": TYPES[extname(p)] || "application/octet-stream" }).end(body); }
  catch { res.writeHead(404).end(); }
}).listen(0, "127.0.0.1");
await new Promise((r) => server.once("listening", r));
const url = `http://127.0.0.1:${server.address().port}/index.html`;

const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
const page = await browser.newPage({ viewport: { width: w, height: h } });
const problems = [];
page.on("console", (m) => { if (["error", "warning"].includes(m.type())) problems.push(`console.${m.type()}: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`uncaught: ${e.message}`));
page.on("requestfailed", (r) => problems.push(`request failed: ${r.url()}`));
page.on("response", (r) => { if (r.status() >= 400) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
await mkdir(dirname(out), { recursive: true });
try {
  await page.goto(url, { waitUntil: "load" });
  for (const a of actions) {
    if (a.wait) await page.waitForTimeout(a.wait);
    else if (a.key) { await page.keyboard.down(a.key); await page.waitForTimeout(a.hold ?? 300); await page.keyboard.up(a.key); }
    else if (a.press) await page.keyboard.press(a.press);
    else if (a.click) await page.mouse.click(a.click[0], a.click[1]);
    else if (a.move) await page.mouse.move(a.move[0], a.move[1]);
    else if (a.shot) { const f = join(dirname(out), a.shot); await page.screenshot({ path: f }); console.log(`screenshot: ${f}`); }
    else if (a.eval) console.log(`eval ${a.eval} => ${JSON.stringify(await page.evaluate(a.eval))}`);
  }
  await page.screenshot({ path: out });
  console.log(`screenshot: ${out}`);
} finally {
  console.log(problems.length ? `browser problems (${problems.length}):\n  ` + problems.slice(0, 30).join("\n  ") : "browser problems: none");
  await browser.close(); server.close();
}
