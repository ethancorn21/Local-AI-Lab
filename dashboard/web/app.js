/* The lab console. Data comes from the labdash server (/api/stream, Server-Sent Events); everything an agent wrote is
   set with textContent, never parsed as HTML. Views: Live (all agents at once), Board (requests + sprint), Hardware,
   and one agent with its task. */
"use strict";
const $ = (q, r = document) => r.querySelector(q);
const SVGNS = "http://www.w3.org/2000/svg";
const GUARD = { alert: 83, stop: 88, cpuStop: 95 };   // the temperature guard on the AI box (server/thermal)
const STALE_SECS = 90;

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(3)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
function s(tag, attrs) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) { if (k === "style") el.style.cssText = v; else el.setAttribute(k, v); }
  return el;
}
function icon(paths) {
  const v = s("svg", { viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 1.6, "stroke-linecap": "round" });
  paths.forEach(d => v.append(s("path", { d })));
  return v;
}
const reloadIcon = () => icon(["M13.5 8a5.5 5.5 0 1 1-1.6-3.9", "M13.5 2.5v3h-3"]);
const growIcon = () => icon(["M9.5 2.5h4v4", "M13.5 2.5 9 7", "M6.5 13.5h-4v-4", "M2.5 13.5 7 9"]);
const dur = sec => { sec = Math.max(0, Math.round(sec)); const hr = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60;
  return hr ? `${hr}h${String(m).padStart(2, "0")}m` : m ? `${m}m${String(sec % 60).padStart(2, "0")}s` : `${sec}s`; };
const clock = t => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const kTok = n => n == null ? "—" : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
const cap1 = t => t ? t[0].toUpperCase() + t.slice(1) : "";
const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };

/* ================================================================ state from the server */
const S = { channels: {}, boards: {}, feed: {}, hw: {}, hist: { minutes: [], temp: {}, power: {} }, skew: 0, online: false };
const SUBS = new Set(), TICKERS = new Set(), ACKS = new Map();
const nowS = () => Date.now() / 1000 + S.skew;
function notify(ch, kind, ev, extra) { for (const f of [...SUBS]) f(ch, kind, ev, extra); }

function indexChannel(c) { c.idx = new Map(c.events.map(e => [e.id, e])); return c; }
function handle(m) {
  switch (m.type) {
    case "snapshot":
      S.channels = {}; for (const [n, c] of Object.entries(m.channels || {})) S.channels[n] = indexChannel(c);
      Object.assign(S, { boards: m.boards || {}, feed: m.feed || {}, hw: m.hw || {}, hist: m.hist || S.hist, skew: (m.now || Date.now() / 1000) - Date.now() / 1000 });
      return go(route, true);
    case "reset":
      S.channels[m.ch] = indexChannel({ meta: m.meta || {}, events: [], snap: null, last: m.last });
      return (route === "live" || route === m.ch) ? go(route, true) : renderChrome();
    case "gone":
      delete S.channels[m.ch];
      return (route === "live" || route === m.ch) ? go(route, true) : renderChrome();
    case "meta": {
      const c = S.channels[m.ch]; if (!c) return;
      const before = [c.meta.task, c.meta.running, c.meta.loop].join("|");
      Object.assign(c.meta, m.meta);
      if (m.last) c.last = m.last;
      notify(m.ch, "meta");
      if ([c.meta.task, c.meta.running, c.meta.loop].join("|") !== before) renderChrome();
      return;
    }
    case "ev": {
      const c = S.channels[m.ch]; if (!c) return;
      c.events.push(m.ev); c.idx.set(m.ev.id, m.ev); c.last = m.last;
      return notify(m.ch, "add", m.ev);
    }
    case "upd": {
      const c = S.channels[m.ch]; if (!c) return;
      const ev = c.idx.get(m.id); if (!ev) return;
      Object.assign(ev, m.set || {});
      for (const [k, v] of Object.entries(m.append || {})) ev[k] = (ev[k] || "") + v;
      c.last = m.last;
      return notify(m.ch, "update", ev, m);
    }
    case "task": { const c = S.channels[m.ch]; if (!c) return; c.snap = m.snap; notify(m.ch, "task"); return renderChrome(); }
    case "board": if (m.board) S.boards[m.project] = m.board; else delete S.boards[m.project]; notify(null, "board"); return renderChrome();
    case "feed": S.feed = m.feed; return renderChrome();
    case "hw": S.hw = m.hw; return notify(null, "hw");
    case "hist": S.hist = m.hist; return notify(null, "hist");
    case "ack": { const f = ACKS.get(m.id); if (f) { ACKS.delete(m.id); f(m); } return; }
  }
}
let es = null;
function connect() {
  if (es) es.close();
  es = new EventSource("/api/stream");
  es.onopen = () => { S.online = true; renderChrome(); };
  es.onerror = () => { S.online = false; renderChrome(); };
  es.onmessage = e => { try { handle(JSON.parse(e.data)); } catch (err) { console.error(err); } };
}
async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Labdash": "1" }, body: JSON.stringify(body) });
  let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

/* ================================================================ derived facts */
const chNames = () => Object.keys(S.channels).sort((a, b) => (S.channels[a].meta.agent || a).localeCompare(S.channels[b].meta.agent || b));
function colorKey(name) {
  const a = (S.channels[name]?.meta.agent || "").toLowerCase();
  if (["a", "b", "c"].includes(a)) return a;
  const free = ["a", "b", "c"].filter(k => !chNames().some(n => (S.channels[n].meta.agent || "").toLowerCase() === k));
  const others = chNames().filter(n => !["a", "b", "c"].includes((S.channels[n].meta.agent || "").toLowerCase()));
  return free[others.indexOf(name)] || null;
}
const chStyle = name => { const k = colorKey(name); return k ? `--ch: var(--ch-${k}); --tint: var(--tint-${k})` : "--ch: var(--fg-2); --tint: var(--sunk)"; };
const letter = name => (S.channels[name]?.meta.agent || name)[0].toUpperCase();
const label = name => { const m = S.channels[name]?.meta || {}; return m.agent ? `agent ${m.agent}` : name; };
const server = name => (S.hw.servers || {})[S.channels[name]?.meta.port] || null;
const gpuOf = name => (S.hw.gpus || []).find(g => g.port && g.port === S.channels[name]?.meta.port) || null;
const chanForPort = port => chNames().find(n => S.channels[n].meta.port === port);
function stale(name) {
  const c = S.channels[name]; if (!c) return false;
  const sv = server(name);
  return !!(c.meta.running && sv && (sv.state === "generating" || sv.state === "reading") && nowS() - (c.last || 0) > STALE_SECS);
}
function speedText(name) {
  const sv = server(name), m = S.channels[name]?.meta || {};
  if (!m.running) return m.loop ? "between sessions" : "loop stopped";
  if (!sv) return cap1(m.activity || "");
  if (sv.state === "generating") return sv.tok_s ? `${sv.tok_s.toFixed(1)} tok/s` : "generating";
  if (sv.state === "reading") return sv.prompt_pct != null ? `reading prompt ${sv.prompt_pct}%` : "reading prompt";
  if (sv.state === "down") return "model server down";
  return cap1(m.activity || "idle");
}
function liveValue(kind, name) {
  const c = S.channels[name]; if (!c) return "";
  const m = c.meta, sv = server(name), g = gpuOf(name);
  if (kind === "speed") return speedText(name);
  if (kind === "sess") return m.running && m.start ? dur(nowS() - m.start) : "—";
  if (kind === "ctx") return `${kTok(m.ctx)}/${kTok(sv?.ctx_max)}`;
  if (kind === "kv") return sv?.kv_pct != null ? `${sv.kv_pct}%` : "—";
  if (kind === "gpu") return g ? `${g.name} · ${Math.round(g.temp)} °C · ${Math.round(g.power)} W` : "—";
  return "";
}
function openAsks() { const out = []; for (const n of chNames()) for (const a of S.channels[n].snap?.asks || []) out.push({ ch: n, ...a }); return out; }

/* ================================================================ rendering events (text only) */
const HL_EXT = /\.(py|js|mjs|cjs|ts|tsx|jsx|sh|bash)$/;
function highlight(text) {
  const frag = document.createDocumentFragment();
  const re = /(#.*$|\/\/.*$)|("[^"]*"|'[^']*'|`[^`]*`)|\b(def|return|if|elif|else|for|while|in|and|or|not|import|from|None|True|False|with|as|class|try|except|raise|const|let|var|function|of|await|async|new|export)\b|\b([A-Za-z_][A-Za-z0-9_]*)(?=\()/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) frag.append(text.slice(last, m.index));
    frag.append(h("span", { class: m[1] ? "c" : m[2] ? "s" : m[3] ? "k" : "fn" }, m[0]));
    last = re.lastIndex;
  }
  if (last < text.length) frag.append(text.slice(last));
  return frag;
}
function body(b) {
  if (!b || typeof b !== "object") return null;
  const hl = HL_EXT.test(String(b.path || ""));
  const txt = t => hl ? highlight(String(t ?? "")) : String(t ?? "");
  if (Array.isArray(b.code)) return h("div", { class: "code v-code" }, b.code.map((l, i) => h("div", { class: "ln" }, h("span", {}, (b.start || 1) + i), h("span", {}), h("span", {}, txt(l)))));
  if (Array.isArray(b.diff)) return h("div", { class: "code v-code" }, b.diff.map(r => {
    const [sign, num, code] = Array.isArray(r) ? r : [" ", "", String(r)];
    return h("div", { class: sign === "+" ? "ln add" : sign === "-" ? "ln del" : "ln" }, h("span", {}, num), h("span", {}, sign === " " ? "" : sign), h("span", {}, txt(code)));
  }));
  if (Array.isArray(b.out)) return h("div", { class: "code v-code" }, b.out.map(r => { const [c, t] = Array.isArray(r) ? r : ["", String(r)]; return h("div", { class: "out " + (c === "f" || c === "p" ? c : "") }, String(t ?? "")); }));
  return null;
}
function meta(ev) {
  const m = ev.meta;
  if (ev.running) return h("span", { class: "meta v-dial" }, "running…");
  if (m && typeof m === "object" && "add" in m) return h("span", { class: "meta v-dial" }, h("span", { class: "plus" }, `+${m.add}`), " ", h("span", { class: "minus" }, `−${m.del}`));
  if (m && typeof m === "object" && "pass" in m) return h("span", { class: "meta v-dial" }, h("span", { class: "p" }, `${m.pass} passed`), m.fail ? [" · ", h("span", { class: "f" }, `${m.fail} failed`)] : null);
  return h("span", { class: "meta v-dial" }, typeof m === "string" ? m : "");
}
const tokens = text => Math.round(String(text || "").length / 3.7);
function thinkHead(ev, node, compact) {
  const sm = h("summary", { class: "label" }, `${ev.done ? "thought" : "thinking"} · ${tokens(ev.text)} tokens`);
  sm.addEventListener("click", e => { if (compact) { e.preventDefault(); node.classList.toggle("open"); } });
  return sm;
}
function render(ev, compact) {
  switch (ev.t) {
    case "divider": return h("div", { class: "divider" }, h("span", { class: "dial" }, ev.time), h("b", {}, ev.task || ""));
    case "sys": return h("div", { class: "sys" + (ev.good ? " good" : "") }, h("span", { class: "label" }, "driver"), h("span", {}, ev.text));
    case "you": return h("div", { class: "you" }, h("p", {}, ev.text), h("small", {}, `you · ${ev.note || ""}`));
    case "say": {
      const p = h("p", { class: "say" }, ev.text || "");
      if (!ev.done) p.append(h("span", { class: "caret" }));
      return p;
    }
    case "think": {
      const p = h("p", {}, ev.text || "");
      if (!ev.done) p.append(h("span", { class: "caret" }));
      const d = h("details", { class: "think" + (ev.done && compact ? " past" : ""), open: true });
      d.append(thinkHead(ev, d, compact), p);
      return d;
    }
    case "tool": {
      const failed = ev.meta && typeof ev.meta === "object" && ev.meta.fail;
      const open = compact ? ["Edit", "Write", "Ask"].includes(ev.kind) || !!failed || !!ev.error : true;
      return h("details", { class: "tool" + (ev.error ? " err" : ""), open },
        h("summary", {}, h("span", { class: "kind label " + ev.kind }, ev.kind), h("span", { class: "target v-code", title: ev.target }, ev.target), meta(ev)),
        body(ev.body));
    }
  }
  return h("div");
}
function updateNode(node, ev, compact, m) {
  if (ev.t === "think" || ev.t === "say") {
    const p = ev.t === "think" ? node.querySelector("p") : node;
    const caret = p.querySelector(".caret");
    p.firstChild && p.firstChild.nodeType === 3 ? (p.firstChild.textContent = ev.text) : p.prepend(ev.text);
    if (ev.t === "think") node.firstChild.replaceWith(thinkHead(ev, node, compact));
    if (ev.done) { if (caret) caret.remove(); if (compact && ev.t === "think") node.classList.add("past"); }
    return node;
  }
  const fresh = render(ev, compact);
  if (node.open !== undefined && ev.t === "tool" && !m?.set?.body) fresh.open = node.open;
  node.replaceWith(fresh);
  return fresh;
}

/* one agent's feed: follows the bottom unless the reader has scrolled up */
function feed(name, compact, scroller, onJump) {
  const el = h("div", { class: "feed" + (compact ? " compact" : "") });
  const nodes = new Map();
  const act = h("div", { class: "activity v-dial" }, h("span", { class: "dots" }, h("i"), h("i"), h("i")), h("span"));
  const paintAct = () => {
    const m = S.channels[name]?.meta || {};
    act.lastChild.textContent = m.running ? cap1(m.activity || "") : (m.loop ? "between sessions" : "loop stopped");
    act.firstChild.hidden = !m.running || /finished/.test(m.activity || "");
  };
  const near = () => { const sc = scroller(); return !sc || sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80; };
  const toBottom = () => { const sc = scroller(); if (sc) sc.scrollTop = sc.scrollHeight; };
  function fill() {
    nodes.clear();
    const evs = S.channels[name]?.events || [];
    el.replaceChildren(...evs.map(ev => { const n = render(ev, compact); nodes.set(ev.id, n); return n; }), act);
    if (!evs.length) el.prepend(h("p", { class: "muted" }, "No session yet."));
    paintAct(); requestAnimationFrame(toBottom);
  }
  function addPending(node) { el.insertBefore(node, act); toBottom(); }
  SUBS.add((ch, kind, ev, m) => {
    if (ch !== name || !el.isConnected) return;
    const pinned = near();
    if (kind === "add") {
      if (ev.t === "you") el.querySelectorAll(".you.pending").forEach(p => { if (String(ev.text).includes(p.dataset.text)) p.remove(); });
      const n = render(ev, compact); nodes.set(ev.id, n); el.insertBefore(n, act);
    } else if (kind === "update" && nodes.has(ev.id)) nodes.set(ev.id, updateNode(nodes.get(ev.id), ev, compact, m));
    else if (kind === "meta") paintAct();
    else return;
    if (pinned) toBottom(); else if (kind !== "meta" && onJump) onJump();
  });
  fill();
  return { el, fill, toBottom, addPending };
}

/* messages: shown at once as pending, confirmed by the shipper's ack, replaced when the agent's session shows it */
async function sendMsg(name, text, stop, f) {
  const note = h("small", {}, "you · sending…");
  const node = h("div", { class: "you pending", "data-text": text }, h("p", {}, text), note);
  f && f.addPending(node);
  try {
    const { id } = await post("/api/message", { ch: name, text, stop });
    ACKS.set(id, a => {
      if (a.ok) note.textContent = stop ? "you · in its inbox: the session stops, the next one starts with this" : "you · in its inbox: read after its current step";
      else { node.classList.add("failed"); note.textContent = `you · not delivered: ${a.error}`; }
    });
  } catch (e) { node.classList.add("failed"); note.textContent = `you · not sent: ${e.message}`; }
}

/* ================================================================ charts and meters */
function niceTicks(min, max, n) {
  const step = Math.pow(10, Math.floor(Math.log10((max - min) / n))), err = (max - min) / n / step;
  const st = step * (err >= 5 ? 10 : err >= 2 ? 5 : err >= 1.5 ? 2 : 1), out = [];
  for (let v = Math.ceil(min / st) * st; v <= max + 1e-9; v += st) out.push(Math.round(v));
  return out;
}
function lineChart(host, { series, minutes, min, max, unit, refs = [], height = 200 }) {
  host.textContent = "";
  const n = minutes.length;
  if (n < 2 || !series.length) { host.append(h("p", { class: "muted" }, "Collecting: the chart fills in over the first minutes.")); return; }
  const W = Math.max(300, host.clientWidth || 600), H = height, m = { l: 34, r: 104, t: 10, b: 22 };
  const x = i => m.l + (W - m.l - m.r) * i / (n - 1), y = v => m.t + (H - m.t - m.b) * (1 - (v - min) / (max - min));
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": series.map(se => `${se.name} ${se.values.at(-1)}${unit}`).join(", ") });
  const text = (attrs, str) => { const t = s("text", attrs); t.textContent = str; svg.append(t); };
  for (const t of niceTicks(min, max, 4)) {
    svg.append(s("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), style: "stroke: var(--line); stroke-width: 1" }));
    text({ x: m.l - 8, y: y(t) + 4, "text-anchor": "end", style: "fill: var(--muted)" }, t);
  }
  [[0, "start"], [Math.floor((n - 1) / 2), "middle"], [n - 1, "end"]].forEach(([i, a]) => text({ x: x(i), y: H - 5, "text-anchor": a, style: "fill: var(--muted)" }, i === n - 1 ? "now" : clock(minutes[i])));
  for (const r of refs) {
    svg.append(s("line", { x1: m.l, x2: W - m.r, y1: y(r.v), y2: y(r.v), style: `stroke: var(${r.color}); stroke-width: 1.5; stroke-dasharray: 4 4` }));
    text({ x: m.l + 6, y: y(r.v) - 5, style: "fill: var(--fg-2)" }, r.label);
  }
  for (const se of series) {
    let d = "", pen = false;
    se.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(Math.max(min, Math.min(max, v))).toFixed(1)}`; pen = true; });
    svg.append(s("path", { d, style: `fill: none; stroke: ${se.color}; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round${se.dashed ? "; stroke-dasharray: 5 3" : ""}` }));
  }
  const ends = series.map(se => ({ se, v: se.values.at(-1) })).filter(e => e.v != null).map(e => ({ ...e, yy: y(e.v) })).sort((p, q) => p.yy - q.yy);
  for (let i = 1; i < ends.length; i++) if (ends[i].yy - ends[i - 1].yy < 15) ends[i].yy = ends[i - 1].yy + 15;
  for (const e of ends) {
    svg.append(s("circle", { cx: x(n - 1), cy: y(e.v), r: 4, style: `fill: ${e.se.color}; stroke: var(--raise); stroke-width: 2` }));
    text({ x: W - m.r + 10, y: e.yy + 4, style: "fill: var(--fg)" }, `${e.se.short} ${Math.round(e.v)}${unit}`);
  }
  const cross = s("line", { y1: m.t, y2: H - m.b, style: "stroke: var(--line-2); stroke-width: 1; visibility: hidden" });
  const dots = series.map(se => s("circle", { r: 4, style: `fill: ${se.color}; stroke: var(--raise); stroke-width: 2; visibility: hidden` }));
  const hit = s("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, style: "fill: transparent" });
  svg.append(cross, ...dots, hit);
  const tip = h("div", { class: "tip v-dial", hidden: true });
  host.append(svg, tip);
  hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.style.visibility = "hidden"; dots.forEach(d => (d.style.visibility = "hidden")); });
  hit.addEventListener("pointermove", ev => {
    const rect = svg.getBoundingClientRect(), sx = (ev.clientX - rect.left) * (W / rect.width);
    const i = Math.max(0, Math.min(n - 1, Math.round((sx - m.l) / (W - m.l - m.r) * (n - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.style.visibility = "visible";
    series.forEach((se, j) => { const v = se.values[i]; if (v == null) { dots[j].style.visibility = "hidden"; return; }
      dots[j].setAttribute("cx", x(i)); dots[j].setAttribute("cy", y(v)); dots[j].style.visibility = "visible"; });
    tip.replaceChildren(h("b", {}, i === n - 1 ? "now" : clock(minutes[i])),
      ...series.map(se => h("div", {}, h("i", { style: `background: ${se.color}` }), h("span", {}, se.name), h("span", {}, se.values[i] == null ? "—" : `${se.values[i]}${unit}`))));
    tip.hidden = false;
    tip.style.left = `${Math.min(x(i) * rect.width / W + 14, rect.width - tip.offsetWidth - 4)}px`; tip.style.top = "6px";
  });
}
function leds(value, min, max, warn, crit, count = 28) {
  const box = h("div", { class: "leds", role: "meter", "aria-valuemin": min, "aria-valuemax": max, "aria-valuenow": value ?? 0 });
  const half = (max - min) / count / 2;
  for (let i = 0; i < count; i++) {
    const seg = min + (max - min) * (i + 0.5) / count;
    let cls = value != null && seg <= value ? "on" : "";
    if (crit != null && seg >= crit) cls += " x"; else if (warn != null && seg >= warn) cls += " w";
    if ((warn != null && Math.abs(seg - warn) <= half) || (crit != null && Math.abs(seg - crit) <= half)) cls += " mark";
    box.append(h("i", { class: cls }));
  }
  return box;
}
function gpuStyle(g) { const n = g.port && chanForPort(g.port); return n ? chStyle(n) : "--ch: var(--fg-2)"; }
function gpuColor(g) { const n = g.port && chanForPort(g.port); const k = n && colorKey(n); return k ? `var(--ch-${k})` : "var(--fg-2)"; }

/* ================================================================ chrome: nav, status, banner */
let route = "live";
function renderChrome() {
  const item = (id, lead, text, sub, tail) => h("button", { "aria-current": String(route === id), onclick: () => go(id) },
    lead, h("span", { class: "grow" }, text, sub ? h("span", { class: "sub" }, sub) : null), tail);
  const asks = openAsks().length;
  const temps = (S.hw.gpus || []).map(g => g.temp).filter(t => t != null);
  const tempText = temps.length ? `${Math.round(Math.min(...temps))}–${Math.round(Math.max(...temps))} °C` : "no data";
  const hot = temps.some(t => t >= GUARD.alert);
  const boards = Object.entries(S.boards);
  $("#nav-main").replaceChildren(h("span", { class: "label" }, "Lab"),
    item("live", h("span", { class: "led " + (chNames().length ? "on" : "") }), "Live", "all agents at once"),
    item("board", h("span", { class: "led" }), "Board", boards.length ? boards.map(([p, b]) => `${p} ${b.done.length}/${b.total}`).join(" · ") : "no sprint yet", asks ? h("span", { class: "n", title: `${asks} request${asks > 1 ? "s" : ""} waiting` }, asks) : null),
    item("hardware", h("span", { class: "led " + (hot ? "warn" : "") }), "Hardware", `GPUs ${tempText}`));
  $("#nav-agents").replaceChildren(h("span", { class: "label" }, "Agents"),
    ...chNames().map(n => { const m = S.channels[n].meta; const st = stale(n);
      return item(n, h("span", { class: "tag", style: `background: var(--ch-${colorKey(n) || "x"}, var(--fg-2))` }, letter(n)), m.task || n,
        `${gpuOf(n)?.name || (m.port ? `port ${m.port}` : "")} · ${m.project || ""}`,
        h("span", { class: "led " + (st ? "warn" : m.running ? "on" : ""), title: st ? "Stream stuck" : m.activity || "" })); }));
  const projects = [...new Set(chNames().map(n => S.channels[n].meta.project))];
  $("#rail-foot").replaceChildren(...projects.map(p => h("p", {}, `${p} · ${chNames().filter(n => S.channels[n].meta.project === p).length} on the console`)),
    h("p", {}, "Hidden projects: hardware only."));
  const stuck = chNames().filter(stale).length;
  $("#status").replaceChildren(...[
    h("span", {}, h("span", { class: "led " + (S.online ? "on" : "warn") }), S.online ? `${chNames().length - stuck} streaming` : "console offline"),
    stuck ? h("span", {}, h("span", { class: "led warn" }), `${stuck} stream stuck`) : null,
    asks ? h("span", {}, h("span", { class: "led warn" }), `${asks} request${asks > 1 ? "s" : ""}`) : null,
    h("span", {}, h("span", { class: "led " + (hot ? "warn" : "") }), `GPUs ${tempText}`)].filter(Boolean));
  const ban = [];
  if (!S.online) ban.push(h("div", { class: "banner crit" }, "The console server is unreachable: is the tunnel up? Retrying every 2 seconds."));
  else if (S.feed && S.feed.connected === false) ban.push(h("div", { class: "banner" }, "The link from the agent VM is down: agent streams are frozen where they stopped. Hardware is live. The shipper reconnects on its own."));
  $("#banner").replaceChildren(...ban);
}

/* ---- live: one tall pane and the others stacked, like terminal windows on one monitor */
function channel(name, slot) {
  const task = h("div", { class: "ch-task" }), metaRow = h("div", { class: "ch-meta v-dial" });
  const reBtn = h("button", { class: "icon-btn", title: `Reconnect ${label(name)}'s stream`, "aria-label": `Reconnect ${label(name)}` }, reloadIcon());
  const growBtn = slot === "big" ? null : h("button", { class: "icon-btn", title: "Make this the tall pane", "aria-label": `Make ${label(name)} the tall pane`,
    onclick: () => { store.set("lab-console-big", name); go("live"); } }, growIcon());
  const warn = h("div", { class: "ch-warn", hidden: true });
  const jump = h("button", { class: "jump", hidden: true }, "↓ new activity");
  const sc = h("div", { class: "ch-feed" });
  const f = feed(name, true, () => sc, () => (jump.hidden = false));
  sc.append(f.el);
  sc.addEventListener("scroll", () => { if (sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80) jump.hidden = true; });
  jump.addEventListener("click", () => { f.toBottom(); jump.hidden = true; });
  const card = h("span", { class: "ch-card v-dial" });
  const paint = () => {
    const m = S.channels[name]?.meta || {};
    card.textContent = [gpuOf(name)?.name, m.project].filter(Boolean).join(" · ");
    task.replaceChildren(h("button", { onclick: () => go(name), title: "Open this agent and its task" }, m.task || "no task yet"));
    metaRow.replaceChildren(...[
      h("span", {}, h("b", { "data-live": `speed:${name}` }, liveValue("speed", name))),
      h("span", {}, "ctx ", h("b", { "data-live": `ctx:${name}` }, liveValue("ctx", name))),
      server(name)?.kv_pct != null ? h("span", {}, "KV pool ", h("b", { "data-live": `kv:${name}` }, liveValue("kv", name))) : null,
      h("span", {}, "session ", h("b", { "data-live": `sess:${name}` }, liveValue("sess", name)))].filter(Boolean));
    const st = stale(name);
    warn.hidden = !st;
    if (st) warn.replaceChildren(h("span", {}, `No updates for ${dur(nowS() - S.channels[name].last)} while the model is working.`),
      h("button", { class: "act attn", onclick: () => reconnect(name, reBtn) }, reloadIcon(), "Reconnect"));
  };
  reBtn.addEventListener("click", () => reconnect(name, reBtn));
  paint();
  SUBS.add((ch, kind) => { if (ch === name && (kind === "meta" || kind === "add")) paint(); else if (kind === "hw") paint(); });
  TICKERS.add(() => { if (!warn.hidden || stale(name)) paint(); });
  const input = h("input", { id: `say-${name}`, placeholder: `Message ${label(name)} · Enter sends, read after its current step`, "aria-label": `Message ${label(name)}`, maxlength: 4000 });
  input.addEventListener("keydown", e => { if (e.key === "Enter" && input.value.trim()) { sendMsg(name, input.value.trim(), false, f); input.value = ""; } });
  return h("section", { class: "channel " + slot, style: chStyle(name) },
    h("div", { class: "ch-head" },
      h("div", { class: "ch-row" }, h("span", { class: "ch-letter" }, letter(name)), card, growBtn, reBtn),
      task, metaRow),
    warn, sc, jump, h("div", { class: "ch-say" }, input));
}
function live() {
  const names = chNames();
  if (!names.length) return h("div", { class: "empty" }, h("p", {}, h("b", {}, "No agents on the console yet. "),
    S.feed.connected ? "The VM link is up but no loop is running in a shipped project." : "Waiting for the shipper on the VM to connect."));
  const saved = store.get("lab-console-big");
  const big = names.includes(saved) ? saved : (names.find(n => S.channels[n].meta.agent === "a") || names[0]);
  const rest = names.filter(n => n !== big);
  const wall = h("div", { class: "wall", style: `grid-template-columns: ${rest.length ? "minmax(0, 1fr) minmax(0, 1fr)" : "minmax(0, 1fr)"}; grid-template-rows: repeat(${Math.max(1, rest.length)}, minmax(0, 1fr))` });
  const bigPane = channel(big, "big");
  bigPane.style.gridArea = `1 / 1 / ${Math.max(1, rest.length) + 1} / 2`;
  wall.append(bigPane);
  rest.forEach((n, i) => { const p = channel(n, "s1"); p.style.gridArea = `${i + 1} / 2 / ${i + 2} / 3`; if (i === rest.length - 1) p.style.borderBottom = "0"; wall.append(p); });
  return wall;
}
async function reconnect(name, btn) {
  btn && (btn.classList.add("spin"), (btn.disabled = true));
  try { await post("/api/resync", name ? { ch: name } : {}); }
  catch (e) { connect(); }
  setTimeout(() => { btn && (btn.classList.remove("spin"), (btn.disabled = false)); }, 1500);
}

/* ---- board: requests waiting for you, then each project's sprint */
function askCard(a) {
  const ta = h("textarea", { id: `ask-${a.ch}-${a.id}`, placeholder: "Answer in your own words…", maxlength: 4000 });
  const status = h("p", { class: "ask-err" });
  const box = h("div", { class: "ask", style: chStyle(a.ch) },
    h("div", { class: "row" }, h("span", { class: "tag", style: `background: var(--ch, var(--fg-2))` }, letter(a.ch)),
      h("span", { class: "label" }, `${label(a.ch)} asks · request ${a.id} · ${a.task || ""}${a.blocking === "yes" ? " · blocking" : ""} · ${a.asked || ""}`)),
    h("p", { class: "ask-text" }, a.text), ta, status,
    h("div", { class: "row" },
      h("button", { class: "act primary", onclick: () => answer(ta.value.trim()) }, "Send answer"),
      h("button", { class: "act", onclick: () => answer("Use your recommendation.") }, "Use its recommendation")));
  async function answer(text) {
    if (!text) { status.textContent = "Type an answer first."; return ta.focus(); }
    status.textContent = "Sending…";
    try {
      const { id } = await post("/api/answer", { ch: a.ch, ask: a.id, text });
      ACKS.set(id, r => { status.textContent = r.ok ? "Answered: the agent's loop continues and its next session reads it." : `Not saved: ${r.error}`; });
    } catch (e) { status.textContent = `Not sent: ${e.message}`; }
  }
  return box;
}
function boardColumns(project, b) {
  const col = (cls, count, text, items, foot) => h("div", { class: "col " + cls },
    h("div", { class: "col-h" }, h("span", { class: "count" }, count), h("span", { class: "label" }, text)), h("ul", {}, items), foot);
  const chFor = agent => chNames().find(n => S.channels[n].meta.project === project && S.channels[n].meta.agent === agent);
  const done = b.done.slice(0, 8).map(t => h("li", {}, h("span", { class: "name" }, t.title), h("span", { class: "id v-dial" }, t.id)));
  const build = b.building.map(t => { const n = t.agent && chFor(t.agent); const m = n ? S.channels[n].meta : null;
    return h("li", { style: n ? chStyle(n) : null }, h("span", { class: "name" }, t.title), h("span", { class: "id v-dial" }, t.id),
      h("span", { class: "note" }, t.agent ? [h("span", { class: "tag", style: "background: var(--ch, var(--fg-2)); width: 18px; height: 18px; font-size: 10.5px" }, t.agent),
        m && m.task_id === t.id ? (stale(n) ? "stream stuck" : (m.running ? m.activity || "working" : "between sessions")) : "claimed",
        t.boxes ? h("span", { class: "boxes", title: `${t.boxes[0]} of ${t.boxes[1]} acceptance boxes ticked` }, Array.from({ length: Math.min(t.boxes[1], 12) }, (_, i) => h("i", { class: i < t.boxes[0] ? "on" : "" }))) : null]
        : (t.status === "split" ? "split: waits for its subtasks" : t.status || "in progress"))); });
  const open = b.open.map(t => h("li", {}, h("span", { class: "name" }, t.title), h("span", { class: "id v-dial" }, t.id),
    h("span", { class: "note" }, t.waits.length ? `after ${t.waits.join(", ")}` : h("span", { class: "ready" }, "ready to start"))));
  return h("div", { class: "board" },
    col("done", b.done.length, "done", done, b.done.length > 8 ? h("div", { class: "more" }, `and ${b.done.length - 8} earlier`) : null),
    col("build", b.building.length, "being built", build),
    col("open", b.open.length, "not started", open));
}
function board() {
  const asksHost = h("div"), boardsHost = h("div", { style: "display: flex; flex-direction: column; gap: 26px" });
  const paintAsks = () => { const asks = openAsks();
    asksHost.replaceChildren(...(asks.length ? [h("div", { class: "sec-h" }, h("h2", {}, "Waiting for you")), h("div", { style: "display: flex; flex-direction: column; gap: 12px" }, asks.map(askCard))] : [])); };
  const paintBoards = () => { const bs = Object.entries(S.boards);
    boardsHost.replaceChildren(...(bs.length ? bs.map(([p, b]) => h("section", {}, h("div", { class: "sec-h" }, h("h2", {}, `Sprint · ${p}`), h("span", { class: "label" }, `${b.done.length} of ${b.total} done · dependencies decide the order`)), boardColumns(p, b)))
      : [h("p", { class: "muted" }, "No sprint yet: a board appears once the shipper sends a project's sprint (every 10 seconds).")])); };
  paintAsks(); paintBoards();
  let askSig = JSON.stringify(openAsks().map(a => a.ch + a.id));
  SUBS.add((ch, kind) => {
    if (kind === "board" || kind === "meta") paintBoards();
    if (kind === "task") { const sig = JSON.stringify(openAsks().map(a => a.ch + a.id)); if (sig !== askSig) { askSig = sig; paintAsks(); } }
  });
  return h("div", { class: "page" }, asksHost, boardsHost);
}

/* ---- hardware */
let hwTable = false;
function hardware() {
  const host = h("div", { style: "display: flex; flex-direction: column; gap: 18px" });
  const tc = h("div", { class: "chart" }), pc = h("div", { class: "chart" });
  const mrow = (lbl, v, max, txt, warn, crit) => h("div", { class: "mrow v-dial" }, h("span", {}, lbl), leds(v, 0, max || 1, warn, crit, 24), h("span", {}, txt));
  const paint = () => {
    const gpus = (S.hw.gpus || []).slice().sort((a, b) => a.index - b.index), cpu = S.hw.cpu || {};
    const cards = gpus.map(g => { const n = g.port && chanForPort(g.port); const sv = g.port ? (S.hw.servers || {})[g.port] : null;
      return h("div", { class: "gauge", style: gpuStyle(g) },
        h("div", { class: "gauge-h" }, h("i"), g.name, h("span", { class: "label" }, n ? `${label(n)} · ${S.channels[n].meta.project}` : g.port ? `port ${g.port} · not on the console` : "no model server")),
        h("div", { class: "readout" }, g.temp != null ? Math.round(g.temp) : "—", h("small", {}, "°C")), leds(g.temp, 30, 95, GUARD.alert, GUARD.stop),
        mrow("power", g.power, g.cap, `${Math.round(g.power ?? 0)} / ${Math.round(g.cap ?? 0)} W`), mrow("fan", g.fan, 100, `${g.fan ?? "—"}%`, null, 95),
        mrow("load", g.util, 100, `${g.util ?? "—"}%`), mrow("memory", g.mem_used, g.mem_total, `${((g.mem_used || 0) / 1024).toFixed(1)}/${((g.mem_total || 0) / 1024).toFixed(1)} GB`),
        h("div", { class: "mrow v-dial" }, h("span", {}, "model"), h("span", {}, sv ? (sv.state === "generating" && sv.tok_s ? `${sv.tok_s.toFixed(1)} tok/s` : sv.state) : "—"), h("span"))); });
    const cpuCard = h("div", { class: "gauge", style: "--ch: var(--fg-2)" },
      h("div", { class: "gauge-h" }, h("i"), "CPU", h("span", { class: "label" }, "AI box")),
      h("div", { class: "readout" }, cpu.temp != null ? Math.round(cpu.temp) : "—", h("small", {}, "°C")), leds(cpu.temp, 30, 100, null, GUARD.cpuStop),
      mrow("load", cpu.load, 100, `${cpu.load ?? "—"}%`), mrow("memory", cpu.ram_used, cpu.ram_total, `${cpu.ram_used ?? "—"}/${cpu.ram_total ?? "—"} GB`));
    const table = h("div", { class: "scroll-x", hidden: !hwTable }, h("table", { class: "hwt v-dial" },
      h("thead", {}, h("tr", {}, ...["card", "temp", "power", "cap", "fan", "load", "memory", "serving"].map(t => h("th", {}, t)))),
      h("tbody", {}, gpus.map(g => h("tr", {}, h("td", {}, g.name), h("td", {}, `${g.temp ?? "—"} °C`), h("td", {}, `${Math.round(g.power ?? 0)} W`),
        h("td", {}, `${Math.round(g.cap ?? 0)} W`), h("td", {}, `${g.fan ?? "—"}%`), h("td", {}, `${g.util ?? "—"}%`),
        h("td", {}, `${((g.mem_used || 0) / 1024).toFixed(1)}/${((g.mem_total || 0) / 1024).toFixed(1)} GB`), h("td", {}, g.port && chanForPort(g.port) ? label(chanForPort(g.port)) : "—"))))));
    const toggle = h("button", { class: "act", onclick: () => { hwTable = !hwTable; paint(); } }, hwTable ? "Hide table" : "Show as table");
    host.replaceChildren(h("div", { class: "hw-cards" }, ...cards, cpuCard),
      h("div", { class: "row", style: "justify-content: space-between; gap: 16px" },
        h("p", { class: "guard" }, `Guard on the AI box: phone alert at ${GUARD.alert} °C for 2 minutes, on throttling, or fans at 95% for 5 minutes. A card at ${GUARD.stop} °C for a minute has its model server stopped; the CPU at ${GUARD.cpuStop} °C stops all of them.`), toggle),
      table);
  };
  const drawCharts = () => {
    const hist = S.hist || {}, gpus = S.hw.gpus || [];
    const ser = key => Object.entries(hist[key] || {}).map(([name, values]) => { const g = gpus.find(x => x.name === name) || {};
      return { name, short: name.replace(/^RTX\s+/, ""), color: g.name ? gpuColor(g) : "var(--fg-2)", dashed: !(g.port && chanForPort(g.port)), values }; });
    lineChart(tc, { series: ser("temp"), minutes: hist.minutes || [], height: 220, min: 30, max: 95, unit: " °C",
      refs: [{ v: GUARD.alert, label: `alert · ${GUARD.alert} °C for 2 min`, color: "--attn" }, { v: GUARD.stop, label: `server stops · ${GUARD.stop} °C for 1 min`, color: "--crit" }] });
    const caps = [...new Set(gpus.map(g => Math.round(g.cap || 0)).filter(Boolean))];
    lineChart(pc, { series: ser("power"), minutes: hist.minutes || [], height: 220, min: 0, max: Math.max(350, ...caps.map(c => c + 30)), unit: " W",
      refs: caps.map(c => ({ v: c, label: `cap ${c} W · ${gpus.filter(g => Math.round(g.cap) === c).map(g => g.name.replace(/^RTX\s+/, "")).join(", ")}`, color: "--line-2" })) });
  };
  paint();
  requestAnimationFrame(drawCharts);
  SUBS.add((ch, kind) => { if (kind === "hw") paint(); if (kind === "hist") drawCharts(); });
  return h("div", { class: "page" }, host,
    h("div", { class: "charts" },
      h("section", {}, h("div", { class: "sec-h" }, h("h2", {}, "Temperature"), h("span", { class: "label" }, "°C · last hour · dashed = not on the console")), tc),
      h("section", {}, h("div", { class: "sec-h" }, h("h2", {}, "Power draw"), h("span", { class: "label" }, "W · last hour")), pc)));
}

/* ---- one agent: its full feed, and everything about its task beside it */
function historyRows(rows) {
  return rows.map(r => {
    const t = v => (v ? String(v).slice(11, 16) : "");
    const bits = [`session ${r.iter}`, r.status_after, r.verify ? `verify: ${r.verify}` : null, r.agent_commits != null ? `${r.agent_commits} commit${r.agent_commits === 1 ? "" : "s"}` : null,
      r.wrapup ? `hand-over: ${r.wrapup}` : null, r.timed_out ? "timed out" : null].filter(Boolean).join(" · ");
    return h("li", {}, h("span", { class: "dial" }, `${t(r.start)}–${t(r.end)}`), h("span", {}, bits));
  });
}
function taskPanel(name) {
  const c = S.channels[name], m = c?.meta || {}, snap = c?.snap || {}, t = snap.task;
  const sec = (lbl, ...kids) => h("section", {}, h("span", { class: "label" }, lbl), ...kids);
  const live = (kind, txt) => h("dd", { "data-live": `${kind}:${name}` }, liveValue(kind, name));
  return [
    h("section", {}, h("span", { class: "label" }, `task ${m.task_id || "—"} · ${t?.status || ""} · session ${m.session ?? "—"}`),
      h("h3", {}, t?.title || m.task || "no task"), t?.goal ? h("p", { class: "goal" }, t.goal) : null),
    ...(snap.asks || []).map(a => sec(`request ${a.id} · waiting for you`, askCard({ ch: name, ...a }))),
    t?.checks?.length ? sec("acceptance", h("ul", { class: "checks" }, t.checks.map(([d, x]) => h("li", { class: d ? "on" : "" }, x)))) : null,
    h("div", { class: "pair" },
      sec("depends on", t?.depends?.length ? h("ul", { class: "links" }, t.depends.map(([id, st]) => h("li", {}, h("span", { class: "id v-dial" }, id), h("span", {}, ""), h("span", { class: "st" + (st === "done" ? " ok" : "") }, st)))) : h("span", { class: "muted" }, "nothing")),
      sec("unlocks", t?.unlocks?.length ? h("ul", { class: "links" }, t.unlocks.map(([id, nm]) => h("li", {}, h("span", { class: "id v-dial" }, id), h("span", {}, nm), h("span")))) : h("span", { class: "muted" }, "nothing waits on it"))),
    t?.touches?.length ? sec("touches", h("ul", { class: "files v-code" }, t.touches.map(f => h("li", {}, h("span", { title: f }, f))))) : null,
    snap.history?.length ? sec("sessions on this task", h("ul", { class: "hist" }, historyRows(snap.history))) : null,
    snap.prep ? sec("prep notes", h("div", { class: "note-box" }, snap.prep)) : null,
    snap.progress ? h("details", { class: "fold" }, h("summary", { class: "label" }, "PROGRESS.md · as the agent wrote it"), h("pre", { class: "progress-note" }, snap.progress)) : null,
    sec("this session", h("dl", { class: "kv v-dial" },
      h("dt", {}, "running"), live("sess"), h("dt", {}, "model"), live("speed"),
      h("dt", {}, h("span", { class: "hint", title: "Tokens in the model's context after its last reply, of the window. The session hands over before it fills." }, "context")), live("ctx"),
      server(name)?.kv_pct != null ? [h("dt", {}, h("span", { class: "hint", title: "Share of the card's context cache (vLLM KV pool) in use" }, "KV pool")), live("kv")] : null,
      h("dt", {}, "gpu"), live("gpu"))),
  ];
}
function agentPage(name) {
  if (!S.channels[name]) return h("div", { class: "empty" }, h("p", {}, h("b", {}, `${name} is not on the console. `), "Its loop may have stopped; it is listed again when it runs."));
  const sc = h("div", { class: "agent-feed" });
  const f = feed(name, false, () => sc);
  sc.append(f.el);
  const conn = h("span", { class: "conn v-dial" });
  const reBtn = h("button", { class: "act", title: "Drop this stream and reconnect (R)" }, reloadIcon(), "Reconnect");
  const paintConn = () => {
    const c = S.channels[name]; if (!c) return;
    const st = stale(name), age = nowS() - (c.last || 0);
    conn.className = "conn v-dial" + (st ? " stale" : "");
    reBtn.className = "act" + (st ? " attn" : "");
    conn.replaceChildren(h("span", { class: "led " + (st ? "warn" : c.meta.running ? "on" : "") }),
      st ? `No updates for ${dur(age)} while the model is working. The stream may be stuck.`
         : `${S.online ? "live" : "offline"} · last update ${dur(age)} ago · ${c.meta.running ? (c.meta.activity || "") : (c.meta.loop ? "between sessions" : "loop stopped")}`);
  };
  reBtn.addEventListener("click", () => reconnect(name, reBtn));
  agentPage.reconnect = () => reconnect(name, reBtn);
  paintConn(); TICKERS.add(paintConn);
  const side = h("aside", { class: "side" }, ...taskPanel(name));
  SUBS.add((ch, kind) => { if (ch === name && (kind === "task" || (kind === "meta" && side.dataset.task !== S.channels[name]?.meta.task_id))) {
    side.dataset.task = S.channels[name]?.meta.task_id || ""; side.replaceChildren(...taskPanel(name).filter(Boolean)); } });
  side.dataset.task = S.channels[name].meta.task_id || "";
  const ta = h("textarea", { id: `msg-${name}`, rows: 2, placeholder: `Message ${label(name)}…`, maxlength: 4000 });
  const send = stop => { const t = ta.value.trim(); if (!t) return ta.focus(); sendMsg(name, t, stop, f); ta.value = ""; };
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(false); } });
  return h("div", { class: "agent", style: chStyle(name) },
    h("div", { class: "agent-main" },
      h("div", { class: "agent-bar" }, conn, reBtn), sc,
      h("div", { class: "composer" }, ta, h("div", { class: "row" }, h("small", {}, "Enter sends · read after its current step · Shift+Enter for a new line"),
        h("div", { class: "row" }, h("button", { class: "act danger", onclick: () => send(true), title: "Ends the running session now; the next one starts with your message" }, "Stop session & send"),
          h("button", { class: "act primary", onclick: () => send(false) }, "Send"))))),
    side);
}

/* ================================================================ routing, ticking */
function go(id, keepScroll) {
  if (!["live", "board", "hardware"].includes(id) && !S.channels[id] && Object.keys(S.channels).length) id = "live";
  route = id; SUBS.clear(); TICKERS.clear();
  const ch = S.channels[id];
  $("#title").textContent = { live: "Live", board: "Board", hardware: "Hardware" }[id] || (ch ? `${label(id)} · ${ch.meta.project || ""}` : id);
  $("#view").replaceChildren(id === "live" ? live() : id === "board" ? board() : id === "hardware" ? hardware() : agentPage(id));
  $("#main").dataset.route = id === "live" ? "live" : ["board", "hardware"].includes(id) ? "page" : "agent";
  if (!keepScroll) $("#main").scrollTop = 0;
  renderChrome();
  store.set("lab-console-route", id);
  if (decodeURIComponent(location.hash.slice(1)) !== id) history.pushState(null, "", "#" + encodeURIComponent(id));
}
$("#reconnect-all").addEventListener("click", async () => {
  const b = $("#reconnect-all"); b.classList.add("spin"); b.disabled = true;
  connect();
  try { await post("/api/resync", {}); } catch (e) {}
  setTimeout(() => { b.classList.remove("spin"); b.disabled = false; }, 1500);
});
document.addEventListener("keydown", e => {
  if (e.key.toLowerCase() === "r" && !e.metaKey && !e.ctrlKey && !e.altKey && !/TEXTAREA|INPUT/.test(document.activeElement.tagName) && S.channels[route]) agentPage.reconnect();
});
let staleSig = "";
setInterval(() => {
  document.querySelectorAll("[data-live]").forEach(el => { const [kind, name] = el.dataset.live.split(/:(.*)/s); el.textContent = liveValue(kind, name); });
  TICKERS.forEach(f => f());
  const sig = chNames().map(stale).join();
  if (sig !== staleSig) { staleSig = sig; renderChrome(); }
}, 1000);
let rz;
window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { if (route === "hardware") notify(null, "hist"); }, 200); });

window.addEventListener("popstate", () => go(decodeURIComponent(location.hash.slice(1)) || "live"));
route = decodeURIComponent(location.hash.slice(1)) || store.get("lab-console-route") || "live";
renderChrome();
connect();
