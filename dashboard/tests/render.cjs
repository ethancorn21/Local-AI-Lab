/* The console page in jsdom, fed a state captured by test_e2e.py through a stand-in EventSource. Prints one JSON line
   per check ({name, ok, detail}). Exit 3 when jsdom is missing, 1 when a check failed.
   node render.cjs <dashboard/web> <state.json> */
"use strict";
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require("jsdom")); } catch (e) { process.exit(3); }
const fs = require("fs"), path = require("path");
const [, , web, stateFile] = process.argv;
const state = JSON.parse(fs.readFileSync(stateFile, "utf8"));
let failed = false;
const check = (name, ok, detail) => { if (!ok) failed = true; console.log(JSON.stringify({ name, ok: !!ok, detail: ok ? "" : String(detail ?? "") })); };

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", e => errors.push(String(e.stack || e)));
vc.on("error", (...a) => errors.push(a.join(" ")));
const html = fs.readFileSync(path.join(web, "index.html"), "utf8").replace(/<script[^>]*><\/script>/g, "").replace(/<link[^>]*stylesheet[^>]*>/g, "");
const dom = new JSDOM(html, { url: "http://localhost:8700/", runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc });
const w = dom.window, d = w.document;
w.addEventListener("error", e => errors.push(String(e.error?.stack || e.message)));

let es;
w.EventSource = class { constructor(url) { es = this; this.url = url; setTimeout(() => { this.onopen?.(); }, 0); } close() {} };
const posts = [], gets = [];
w.fetch = async (url, opts) => {
  if (!opts || opts.method !== "POST") { gets.push(url); return { ok: true, status: 200, json: async () => ({ body: { diff: [["-", "4", "old"], ["+", "4", "new()"]], path: "web/feed.py" } }) }; }
  posts.push({ url, body: JSON.parse(opts.body), headers: opts.headers }); return { ok: true, status: 202, json: async () => ({ id: "c" + posts.length }) };
};
// jsdom has no layout: the timeline's canvas gets a width so it draws
Object.defineProperty(w.HTMLElement.prototype, "clientWidth", { configurable: true, get() { return this.classList?.contains("tl-stage") ? 1400 : 0; } });
const navTo = name => [...d.querySelectorAll("#nav-main button")].find(b => b.textContent.startsWith(name))?.click();
const push = m => es.onmessage({ data: JSON.stringify(m) });
const tick = (ms = 30) => new Promise(r => setTimeout(r, ms));
const text = sel => [...d.querySelectorAll(sel)].map(e => e.textContent);

(async () => {
  w.eval(fs.readFileSync(path.join(web, "app.js"), "utf8"));
  await tick();
  push({ type: "snapshot", ...state });
  await tick(60);
  const names = Object.keys(state.channels);
  const name = "demo.a", ch = state.channels[name];
  const tools = ch.events.filter(e => e.t === "tool").length;
  const pane = () => d.getElementById(`say-${name}`)?.closest(".channel");   // this agent's pane on the live wall

  // live
  check("live: one pane per agent", d.querySelectorAll("#view .channel").length === names.length, d.querySelectorAll("#view .channel").length);
  check("live: pane shows the task", text("#view .ch-task").some(t => t === ch.meta.task), text("#view .ch-task"));
  check("live: every tool call in the pane", pane()?.querySelectorAll(".tool").length === tools, pane()?.querySelectorAll(".tool").length);
  check("live: HTML from the agent stays text", !w.PWNED && !d.querySelector("#view img") && !d.querySelector("#view .say b")
        && d.getElementById("view").textContent.includes('<img src=x onerror="window.PWNED=1">'));
  check("nav: agent listed, request counted", text("#nav-agents button").length === names.length && d.querySelector("#nav-main .n")?.textContent === "1");
  const chrome = () => text("#view .ch-head, #view .side dl, #view .agent-bar, #nav-main, #nav-agents, #status, #view .gauge, #view .board .note").join(" ");
  check("live: no null or undefined in headers", !/\b(null|undefined|NaN)\b/.test(chrome()), chrome().match(/.{30}\b(null|undefined|NaN)\b.{0,10}/)?.[0]);
  check("labels: vivid chips for thinking, tools, driver", ["k-think", "k-bash", "k-driver"].every(k => pane().querySelector(`.chip.${k}`)),
        ["k-think", "k-bash", "k-driver", "k-read", "k-edit", "k-say"].map(k => `${k}:${pane().querySelectorAll(".chip." + k).length}`).join(" "));
  check("labels: a state chip in every pane header", d.querySelectorAll("#view .ch-head .chip.state").length === names.length);
  check("status: streaming count", d.getElementById("status").textContent.includes(`${names.length} streaming`), d.getElementById("status").textContent);

  // live updates: a new block, streamed text, a finished tool call
  const n0 = pane().querySelectorAll(".say").length;
  push({ type: "ev", ch: name, ev: { id: "9-1", t: "say", text: "Streaming", done: false }, last: Date.now() / 1000 });
  push({ type: "upd", ch: name, id: "9-1", append: { text: " more" }, last: Date.now() / 1000 });
  push({ type: "ev", ch: name, ev: { id: "9-2", t: "tool", kind: "Edit", target: "web/feed.py", meta: "", running: true }, last: Date.now() / 1000 });
  push({ type: "upd", ch: name, id: "9-2", set: { running: false, meta: { add: 2, del: 1 }, body: { diff: [["-", "4", "old"], ["+", "4", "new()"], ["+", "5", "x"]], path: "web/feed.py" } }, last: Date.now() / 1000 });
  await tick();
  const says = pane().querySelectorAll(".say");
  check("live: new block appended and streamed", says.length === n0 + 1 && says[says.length - 1].textContent.startsWith("Streaming more"), says[says.length - 1]?.textContent);
  const lastTool = [...pane().querySelectorAll(".tool")].pop();
  check("live: tool call finished in place with its diff", lastTool?.querySelectorAll(".ln.add").length === 2 && lastTool.querySelector(".meta").textContent.includes("+2"),
        lastTool?.outerHTML.slice(0, 200));
  push({ type: "meta", ch: name, meta: { task: "a new task", task_id: "005" } });
  await tick();
  check("live: meta change repaints the header and nav", text("#view .ch-task").includes("a new task") && text("#nav-agents button").some(t => t.includes("a new task")));

  // messaging from the pane
  const input = d.getElementById(`say-${name}`);
  input.value = "hello there";
  input.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter" }));
  await tick();
  check("message: posted with the CSRF header", posts.at(-1)?.url === "/api/message" && posts.at(-1).headers["X-Labdash"] === "1" && posts.at(-1).body.text === "hello there");
  check("message: shown as pending", d.querySelector("#view .you.pending")?.textContent.includes("hello there"));
  push({ type: "ack", id: "c" + posts.length, ok: true });
  await tick();
  check("message: ack updates the note", d.querySelector("#view .you.pending small")?.textContent.includes("in its inbox"), d.querySelector("#view .you.pending small")?.textContent);
  push({ type: "ev", ch: name, ev: { id: "9-3", t: "you", text: "hello there", note: "delivered" }, last: Date.now() / 1000 });
  await tick();
  check("message: pending replaced by the delivered one", !d.querySelector("#view .you.pending") && text("#view .you").some(t => t.includes("hello there")));

  // board
  navTo("Board");
  await tick();
  check("board: no null or undefined", !/\b(null|undefined|NaN)\b/.test(chrome()), chrome().match(/.{30}\b(null|undefined|NaN)\b.{0,10}/)?.[0]);
  check("board: request waiting", d.querySelectorAll("#view .ask").length === 1 && d.querySelector("#view .ask-text").textContent === ch.snap.asks[0].text);
  const cols = [...d.querySelectorAll("#view .board .col")].map(c => c.querySelectorAll("li").length);
  const b = state.boards.demo;
  const ready = b.open.filter(t => !t.waits.length).length;
  check("board: done / being built / ready / waiting", JSON.stringify(cols) === JSON.stringify([Math.min(8, b.done.length), b.building.length, ready, b.open.length - ready]), cols);
  check("board: ready column loud when it has work", text("#view .col.ready-col .note").join("|") === "ready to start" && !!d.querySelector("#view .col.ready-col.has"), text("#view .col.ready-col .note"));
  check("board: a waiting task names its blocker and who builds it", text("#view .col.open .dep").join("|") === "002a" && text("#view .col.open .dep b").join("|") === "a", text("#view .col.open .dep"));
  d.querySelector("#view .ask textarea").value = "Relative.";
  d.querySelector("#view .ask .act.primary").click();
  await tick();
  check("board: answer posted", posts.at(-1)?.url === "/api/answer" && posts.at(-1).body.ask === "001" && posts.at(-1).body.text === "Relative.");

  // hardware, with cards, a model server and an hour of history
  const minutes = Array.from({ length: 30 }, (_, i) => Math.floor(Date.now() / 60000) * 60 - (29 - i) * 60);
  push({ type: "hw", hw: { gpus: [
    { index: 0, name: "RTX 3090", temp: 71, power: 240, cap: 250, fan: 60, util: 98, mem_used: 22000, mem_total: 24576, port: "8081" },
    { index: 2, name: "RTX 3090 Ti", temp: 84, power: 245, cap: 250, fan: 75, util: 99, mem_used: 23000, mem_total: 24576, port: "8080" }],
    cpu: { temp: 55, load: 12, ram_used: 20.1, ram_total: 62.6 }, servers: { "8080": { state: "generating", tok_s: 31.4, kv_pct: 42, ctx_max: 150000 } } } });
  push({ type: "hist", hist: { minutes, temp: { "RTX 3090": minutes.map((_, i) => 60 + i % 7), "RTX 3090 Ti": minutes.map((_, i) => i === 5 ? null : 70 + i % 5) },
                                power: { "RTX 3090": minutes.map(() => 240), "RTX 3090 Ti": minutes.map(() => 245) } } });
  navTo("Hardware");
  await tick(80);
  check("hardware: no null or undefined", !/\b(null|undefined|NaN)\b/.test(chrome()), chrome().match(/.{30}\b(null|undefined|NaN)\b.{0,10}/)?.[0]);
  check("hardware: a card per GPU plus the CPU", d.querySelectorAll("#view .gauge").length === 3, d.querySelectorAll("#view .gauge").length);
  check("hardware: card names its agent", text("#view .gauge-h").some(t => t.includes("RTX 3090 Ti") && t.includes("agent a")), text("#view .gauge-h"));
  check("hardware: two charts drawn, gap left open", d.querySelectorAll("#view .chart svg").length === 2
        && [...d.querySelectorAll("#view .chart svg path")].some(p => (p.getAttribute("d").match(/M/g) || []).length === 2));
  check("hardware: hot card lights the nav", [...d.querySelectorAll("#nav-main button")].find(b => b.textContent.startsWith("Hardware"))?.querySelector(".led.warn") != null);

  // timeline
  navTo("Timeline");
  await tick(80);
  const fl = state.flow || {};
  check("timeline: a row per agent plus main", d.querySelectorAll("#view .tl-gutter").length === names.length + 1, d.querySelectorAll("#view .tl-gutter").length);
  check("timeline: file changes drawn as nodes", d.querySelectorAll("#view .tl-node").length > 0, d.querySelectorAll("#view .tl-node").length);
  check("timeline: sessions as bands, a past one done", d.querySelectorAll("#view .tl-band").length >= 2 && d.querySelector("#view .tl-band.done") != null, d.querySelectorAll("#view .tl-band").length);
  check("timeline: failing test run and merge marked", d.querySelector("#view .tl-test.f") != null && d.querySelector("#view .tl-world .tl-merged") != null);
  check("timeline: waiting hatched", d.querySelector("#view rect.tl-wait") != null);
  check("timeline: no null or undefined", !/\b(null|undefined|NaN)\b/.test(d.getElementById("view").textContent), d.getElementById("view").textContent.match(/.{30}\b(null|undefined|NaN)\b.{0,10}/)?.[0]);
  d.querySelector("#view .tl-node").click();
  await tick(60);
  check("timeline: a node opens its diff", !d.querySelector("#view .tl-drawer").hidden && gets.at(-1)?.startsWith("/api/flow/body?ch=") && d.querySelectorAll("#view .tl-drawer .ln.add").length === 1,
        gets.at(-1));
  const nb = d.querySelectorAll("#view .tl-node").length;
  const ch0 = names.find(n => (fl[n]?.nodes || []).length);
  push({ type: "flow_node", ch: ch0, node: { id: "99:e1", iter: 99, t: Date.now() / 1000 - 2 * 3600, kind: "Write", path: "web/new_file.py", add: 12, del: 0, approx: false } });
  await tick(60);
  check("timeline: a live change appears", d.querySelectorAll("#view .tl-node").length === nb + 1 && text("#view .tl-node").some(t => t.includes("new_file.py")));

  // theme
  const t0 = d.documentElement.dataset.theme;
  d.getElementById("theme").click();
  check("theme: toggles and is remembered", d.documentElement.dataset.theme !== t0 && w.localStorage.getItem("lab-console-theme") === d.documentElement.dataset.theme);

  // one agent
  d.querySelector("#nav-agents button").click();
  await tick(60);
  check("agent: no null or undefined", !/\b(null|undefined|NaN)\b/.test(chrome()), chrome().match(/.{30}\b(null|undefined|NaN)\b.{0,10}/)?.[0]);
  check("agent: full feed", d.querySelectorAll("#view .agent-feed .tool").length === tools + 1, d.querySelectorAll("#view .agent-feed .tool").length);
  check("agent: task panel", d.querySelector("#view .side h3")?.textContent === ch.snap.task.title && d.querySelectorAll("#view .side .checks li").length === ch.snap.task.checks.length,
        d.querySelector("#view .side h3")?.textContent);
  check("agent: model line from the server", text("#view .side dd").some(t => t.includes("31.4 tok/s")), text("#view .side dd"));
  check("agent: request with an answer form", d.querySelectorAll("#view .side .ask textarea").length === 1);
  const ta = d.getElementById(`msg-${name}`);
  ta.value = "stop now";
  [...d.querySelectorAll("#view .composer .act")].find(x => x.textContent.includes("Stop"))?.click();
  await tick();
  check("agent: stop & send posts stop", posts.at(-1)?.body.stop === true && posts.at(-1).body.text === "stop now");
  d.querySelector("#view .agent-bar .act").click();
  await tick();
  check("agent: reconnect resyncs this agent", posts.at(-1)?.url === "/api/resync" && posts.at(-1).body.ch === name);
  push({ type: "gone", ch: name });
  await tick();
  check("agent: gone agent leaves the console", text("#nav-agents button").length === names.length - 1, text("#nav-agents button"));
  push({ type: "board", project: "demo", board: null });
  await tick();
  check("board: a dropped board leaves the nav", text("#nav-main button").some(t => t.includes("no sprint yet")), text("#nav-main button"));

  // the links down
  push({ type: "feed", feed: { connected: false } });
  await tick();
  check("banner: VM link down", d.getElementById("banner").textContent.includes("link from the agent VM is down"));
  es.onerror();
  await tick();
  check("banner: console offline", d.getElementById("banner").textContent.includes("unreachable"));

  check("all views: no null or undefined in headers", !/\b(null|undefined|NaN)\b/.test(chrome()), chrome().match(/.{30}\b(null|undefined|NaN)\b.{0,10}/)?.[0]);
  check("no script errors", errors.length === 0, errors.slice(0, 3).join(" || "));
  process.exit(failed ? 1 : 0);
})().catch(e => { check("page script ran", false, e.stack); process.exit(1); });
