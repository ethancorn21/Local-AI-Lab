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
/* labels: one vivid chip per kind of event (colours in app.css, --k-*) */
const KIND = { Bash: "k-bash", Read: "k-read", Edit: "k-edit", Write: "k-write", Search: "k-web", Fetch: "k-web", Ask: "k-ask" };
const chip = (k, text, more) => h("span", { class: `chip ${k}${more ? " " + more : ""}` }, text);

/* ================================================================ state from the server */
const S = { channels: {}, flow: {}, boards: {}, feed: {}, hw: {}, hist: { minutes: [], temp: {}, power: {} }, skew: 0, online: false };
const SUBS = new Set(), TICKERS = new Set(), ACKS = new Map();
const nowS = () => Date.now() / 1000 + S.skew;
function notify(ch, kind, ev, extra) { for (const f of [...SUBS]) f(ch, kind, ev, extra); }

function indexChannel(c) { c.idx = new Map(c.events.map(e => [e.id, e])); return c; }
function flowOf(ch) { return S.flow[ch] || (S.flow[ch] = { since: null, done: false, sessions: new Map(), nodes: new Map(), marks: new Map() }); }
function handle(m) {
  switch (m.type) {
    case "snapshot":
      S.channels = {}; for (const [n, c] of Object.entries(m.channels || {})) S.channels[n] = indexChannel(c);
      S.flow = {}; for (const [n, f] of Object.entries(m.flow || {})) S.flow[n] = { since: f.since, done: f.done,
        sessions: new Map((f.sessions || []).map(x => [x.iter, x])), nodes: new Map((f.nodes || []).map(x => [x.id, x])), marks: new Map((f.marks || []).map(x => [x.id, x])) };
      Object.assign(S, { boards: m.boards || {}, feed: m.feed || {}, hw: m.hw || {}, hist: m.hist || S.hist, skew: (m.now || Date.now() / 1000) - Date.now() / 1000 });
      return go(route, true);
    case "reset":
      S.channels[m.ch] = indexChannel({ meta: m.meta || {}, events: [], snap: null, last: m.last });
      return (route === "live" || route === m.ch) ? go(route, true) : renderChrome();
    case "gone":
      delete S.channels[m.ch]; delete S.flow[m.ch];
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
    case "flow_span": flowOf(m.ch).since = m.since; return notify(m.ch, "flow");
    case "flow_done": flowOf(m.ch).done = true; return notify(m.ch, "flow");
    case "flow_sess": flowOf(m.ch).sessions.set(m.sess.iter, m.sess); return notify(m.ch, "flow");
    case "flow_mark": flowOf(m.ch).marks.set(m.mark.id, m.mark); return notify(m.ch, "flow");
    case "flow_node": { const f = flowOf(m.ch), old = f.nodes.get(m.node.id);
      if (!(old && !old.approx && m.node.approx)) f.nodes.set(m.node.id, m.node); return notify(m.ch, "flow"); }
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
const chStyle = name => { const k = colorKey(name); return k ? `--ch: var(--ch-${k}); --tint: color-mix(in oklab, var(--ch-${k}) var(--wash), var(--raise))` : "--ch: var(--fg-2); --tint: var(--sunk)"; };
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
/* what an agent is doing right now, as one chip: the pane headers, the agent page, the board */
function stateOf(name) {
  const m = S.channels[name]?.meta || {}, a = String(m.activity || "");
  if (!m.running) {
    if (!m.loop) return { k: "k-wait", text: "loop stopped", quiet: true };
    if (/nothing to take now|waiting (up to|for)/.test(m.driver || "")) return { k: "k-wait", text: "waiting" };
    return { k: "k-driver", text: "driver", live: true };
  }
  if (stale(name)) return { k: "k-stuck", text: "stuck?" };
  if (/^thinking/.test(a)) return { k: "k-think", text: "thinking", live: true };
  if (/^answering/.test(a)) return { k: "k-say", text: "answering", live: true };
  const t = a.match(/^(?:running|writing an? )\s*(\w+)/i);
  if (t) { const raw = t[1].toLowerCase(), kind = { web_search: "Search", web_fetch: "Fetch", ask_human: "Ask" }[raw] || cap1(raw);
    return { k: KIND[kind] || "k-tool", text: /^writing/.test(a) ? `writing ${kind}` : kind, live: true }; }
  if (/reading prompt/.test(a)) return { k: "k-tool", text: "reading prompt", live: true };
  if (/finished/.test(a)) return { k: "k-driver", text: "driver", live: true };
  return { k: "k-tool", text: a || "working", live: true };
}
function stateChip(name, extra) {
  const st = stateOf(name);
  const el = chip(st.k, [h("i"), st.text], `state${st.live ? " live" : ""}${st.quiet ? " quiet" : ""}${extra ? " " + extra : ""}`);
  el.title = S.channels[name]?.meta.running ? S.channels[name].meta.activity || "" : S.channels[name]?.meta.driver || "";
  return el;
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
  const sm = h("summary", {}, chip("k-think", ev.done ? "thought" : "thinking", "lead" + (ev.done ? "" : " live")), h("span", { class: "muted" }, `${tokens(ev.text)} tokens`));
  sm.addEventListener("click", e => { if (compact) { e.preventDefault(); node.classList.toggle("open"); } });
  return sm;
}
function render(ev, compact) {
  switch (ev.t) {
    case "divider": return h("div", { class: "divider" }, h("span", { class: "dial" }, ev.time), ev.prep ? chip("k-prep", "prep") : null, h("b", {}, ev.task || ""));
    case "sys": return h("div", { class: "sys" + (ev.good ? " good" : "") }, chip("k-driver", "driver", "lead"), h("span", {}, ev.text));
    case "you": return h("div", { class: "you" }, h("p", {}, ev.text), h("small", {}, `you · ${ev.note || ""}`));
    case "say": {
      const p = h("p", { class: "say" }, ev.text || "");
      if (!ev.done) p.append(h("span", { class: "caret" }));
      return h("div", { class: "sayblock" }, chip("k-say", ev.done ? "answer" : "answering", "lead" + (ev.done ? "" : " live")), p);
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
        h("summary", {}, chip(KIND[ev.kind] || "k-tool", ev.kind, "kind lead" + (ev.running ? " live" : "")), h("span", { class: "target v-code", title: ev.target }, ev.target), meta(ev)),
        body(ev.body));
    }
  }
  return h("div");
}
function updateNode(node, ev, compact, m) {
  if (ev.t === "think" || ev.t === "say") {
    const p = node.querySelector("p");
    const caret = p.querySelector(".caret");
    p.firstChild && p.firstChild.nodeType === 3 ? (p.firstChild.textContent = ev.text) : p.prepend(ev.text);
    if (ev.t === "think") node.firstChild.replaceWith(thinkHead(ev, node, compact));
    if (ev.t === "say" && ev.done) node.firstChild.replaceWith(chip("k-say", "answer", "lead"));
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
    item("flow", h("span", { class: "led " + (chNames().length ? "on" : "") }), "Timeline", "file changes · sprint"),
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
  const stateHost = h("span", { class: "ch-state" });
  const paint = () => {
    const m = S.channels[name]?.meta || {};
    stateHost.replaceChildren(...[stateChip(name), m.prep && m.running ? chip("k-prep", "prep") : null].filter(Boolean));
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
      h("div", { class: "ch-row" }, h("span", { class: "ch-letter" }, letter(name)), stateHost, card, growBtn, reBtn),
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
        m && m.task_id === t.id ? stateChip(n) : "claimed",
        t.boxes ? h("span", { class: "boxes", title: `${t.boxes[0]} of ${t.boxes[1]} acceptance boxes ticked` }, Array.from({ length: Math.min(t.boxes[1], 12) }, (_, i) => h("i", { class: i < t.boxes[0] ? "on" : "" }))) : null]
        : (t.status === "split" ? "split: waits for its subtasks" : t.status || "in progress"))); });
  // Not started splits in two: work an idle agent could take now, and work that waits on other tasks (with who holds
  // those), so blocked work never reads as unassigned (frontpage 2026-10-07: "not started" held 234-238, all behind 256).
  const holder = id => (b.building.find(x => x.id === id) || {}).agent;
  const ready = b.open.filter(t => !t.waits.length), waiting = b.open.filter(t => t.waits.length);
  const readyLi = ready.map(t => h("li", {}, h("span", { class: "name" }, t.title), h("span", { class: "id v-dial" }, t.id),
    h("span", { class: "note" }, h("span", { class: "ready" }, "ready to start"))));
  const waitLi = waiting.map(t => h("li", {}, h("span", { class: "name" }, t.title), h("span", { class: "id v-dial" }, t.id),
    h("span", { class: "note" }, "after", ...t.waits.map(id => { const a = holder(id);
      if ((t.missing || []).includes(id)) return h("span", { class: "dep", title: "named in Depends on, no task file yet" }, id, h("b", {}, "not written"));
      return h("span", { class: "dep", style: a && chFor(a) ? chStyle(chFor(a)) : null }, id, a ? h("b", {}, a) : null); }))));
  return h("div", { class: "board" },
    col("done", b.done.length, "done", done, b.done.length > 8 ? h("div", { class: "more" }, `and ${b.done.length - 8} earlier`) : null),
    col("build", b.building.length, "being built", build),
    col("ready-col" + (ready.length ? " has" : ""), ready.length, "ready to start", readyLi),
    col("open", waiting.length, "waiting on other tasks", waitLi));
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

/* ---- timeline: each agent's file changes over the sprint, like a node canvas. Time runs left to right; one row per
   agent and one for main. A node is an edit or a write (click: its diff); sessions are bands, tests are ✓/✗ pills,
   merges arc up into main, waiting is hatched. Drag or swipe to pan, pinch / Ctrl+wheel / the wheel to zoom.
   History sessions place their changes by position in the session file (no clock in Pi's events): "≈" times. */
const TL = { spp: 30, right: null, follow: true, project: null, open: null };   // seconds per pixel; right edge (null = now)
const TL_STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
// a lane, top to bottom: the driver's marks, session bands, rows of changes, "+N" for changes with no room, test runs
const LANE = { marks: 22, bands: 24, row: 30, rows: 3, more: 20, tests: 22, pad: 8 }, MAIN_H = 44, AXIS_H = 26, GUTTER = 156;
LANE.head = LANE.marks + LANE.bands;
const laneH = LANE.head + LANE.row * LANE.rows + LANE.more + LANE.tests + LANE.pad;
/* a row of labels in time order without overlaps: important ones placed first, the rest where they still fit */
function packRow(items, gap = 4) {
  const taken = [], fits = (a, b) => taken.every(([l, r]) => b + gap <= l || a >= r + gap);
  const out = [];
  for (const pass of [true, false]) for (const it of items) if (!!it.important === pass && fits(it.x, it.x + it.w)) { taken.push([it.x, it.x + it.w]); out.push(it); }
  return out;
}
const textW = (str, px = 6.4) => 14 + px * String(str).length;
const base = p => String(p || "").split("/").pop();
const MARKS = { merged: ["tl-merged", "merged"], handed: ["tl-handed", "handed over"], split: ["tl-quiet", "split"], "new task": ["tl-quiet", "new task"],
  stalled: ["tl-bad", "stalled"], timeout: ["tl-bad", "timed out"], paused: ["tl-quiet", "paused"] };
function timeline() {
  const projects = [...new Set(chNames().map(n => S.channels[n].meta.project))];
  if (!projects.length) return h("div", { class: "empty" }, h("p", {}, h("b", {}, "No agents on the console yet. "), "The timeline fills in once the shipper sends a project's agents."));
  if (!projects.includes(TL.project)) TL.project = projects[0];
  const lanes = chNames().filter(n => S.channels[n].meta.project === TL.project);
  const stage = h("div", { class: "tl-stage" }), world = h("div", { class: "tl-world" }), svg = s("svg", { class: "tl-svg" });
  world.append(svg); stage.append(world);
  const drawer = h("aside", { class: "tl-drawer", hidden: true });
  const status = h("span", { class: "label" }), followBtn = h("button", { class: "act", "aria-pressed": "true" }, "Follow now");
  const rightT = () => (TL.follow || TL.right == null) ? nowS() + 50 * TL.spp : TL.right;
  const setView = (spp, right, follow) => { TL.spp = Math.max(2, Math.min(3600, spp)); TL.right = right; TL.follow = follow; schedule(); };
  const span = () => { const all = lanes.map(n => S.flow[n]?.since).filter(Boolean); return all.length ? Math.min(...all) : nowS() - 6 * 3600; };
  const fit = () => { const w = Math.max(200, stage.clientWidth - GUTTER - 40); setView((nowS() - span()) / w, null, true); };
  const zoomBy = (f, cx) => {   // keep the time under cx where it is
    const w = stage.clientWidth, left = rightT() - (w - GUTTER) * TL.spp, ta = left + ((cx ?? w) - GUTTER) * TL.spp;
    const spp = Math.max(2, Math.min(3600, TL.spp * f)), nl = ta - ((cx ?? w) - GUTTER) * spp, nr = nl + (w - GUTTER) * spp;
    setView(spp, nr, TL.follow && cx == null);
  };
  let raf = 0;
  const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); };
  function el(cls, x, y, kids, attrs) { const e = h("div", { class: "tl-el " + cls, ...(attrs || {}) }, kids); e.style.left = `${x}px`; e.style.top = `${y}px`; world.append(e); return e; }
  function draw() {
    if (!stage.isConnected) return;
    followBtn.setAttribute("aria-pressed", String(TL.follow)); followBtn.classList.toggle("on", TL.follow);
    const loadingLanes = lanes.filter(n => !S.flow[n]?.done).map(label);
    status.textContent = loadingLanes.length ? `loading history: ${loadingLanes.join(", ")}…` : `since ${S.flow[lanes[0]]?.since ? new Date(span() * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : "—"}`;
    const W = stage.clientWidth; if (!W) return;
    const right = rightT(), left = right - (W - GUTTER) * TL.spp, x = t => GUTTER + (t - left) / TL.spp;
    const H = AXIS_H + MAIN_H + lanes.length * laneH;
    world.querySelectorAll(".tl-el").forEach(e => e.remove());
    svg.replaceChildren(); svg.setAttribute("width", W); svg.setAttribute("height", H); world.style.height = `${H}px`;
    const defs = s("defs"), pat = s("pattern", { id: "tl-hatch", width: 7, height: 7, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
    pat.append(s("rect", { width: 7, height: 7, style: "fill: var(--raise)" }), s("line", { x1: 0, y1: 0, x2: 0, y2: 7, style: "stroke: var(--line-2); stroke-width: 2.5" }));
    defs.append(pat); svg.append(defs);
    // time axis and grid
    const step = TL_STEPS.find(st => st / TL.spp >= 96) || 86400, tzo = new Date().getTimezoneOffset() * 60;
    for (let t = Math.ceil((left - tzo) / step) * step + tzo; t <= right; t += step) {
      const xx = x(t); if (xx < GUTTER) continue;
      const midnight = (t - tzo) % 86400 === 0;
      svg.append(s("line", { x1: xx, x2: xx, y1: AXIS_H - 4, y2: H, style: `stroke: var(--line); stroke-width: ${midnight ? 2 : 1}` }));
      el("tl-tick v-dial", xx + 4, 5, midnight ? new Date(t * 1000).toLocaleDateString([], { weekday: "short", day: "numeric" }) : clock(t));
    }
    // main: merges from every agent
    const mainY = AXIS_H, mid = mainY + MAIN_H / 2;
    svg.append(s("line", { x1: GUTTER, x2: W, y1: mid, y2: mid, style: "stroke: var(--good); stroke-width: 2; opacity: 0.55" }));
    el("tl-gutter", 0, mainY, [h("span", { class: "tl-main-tag" }, "main"), h("span", { class: "muted" }, "merged work")]).style.height = `${MAIN_H}px`;
    const merges = [];
    lanes.forEach((n, li) => {
      const f = S.flow[n] || flowOf(n), y0 = AXIS_H + MAIN_H + li * laneH, sty = chStyle(n);
      if (li % 2 === 0) svg.append(s("rect", { x: GUTTER, y: y0, width: W - GUTTER, height: laneH, style: "fill: var(--sunk); opacity: 0.35" }));
      svg.append(s("line", { x1: 0, x2: W, y1: y0, y2: y0, style: "stroke: var(--line)" }));
      const sessions = [...f.sessions.values()].sort((a, b) => a.start - b.start);
      const endOf = (sx, i) => Math.max(sx.start + 30, sx.end ?? sessions[i + 1]?.start ?? nowS());   // a clock jump can put the end before the start
      // waiting: hatched from the driver's "nothing to take" to the next session
      for (const m of f.marks.values()) if (m.kind === "wait") {
        const nxt = sessions.find(sx => sx.start > m.t), e = nxt ? nxt.start : nowS();
        if (e < left || m.t > right) continue;
        const x1 = Math.max(GUTTER, x(m.t)), x2 = Math.min(W, x(e));
        if (x2 - x1 < 2) continue;
        svg.append(s("rect", { class: "tl-wait", x: x1, y: y0 + 3, width: x2 - x1, height: laneH - 6, fill: "url(#tl-hatch)", opacity: 0.8 }));
        if (x2 - x1 > 70) el("tl-waitlbl v-dial", x1 + 6, y0 + LANE.marks + 5, `waiting ${dur(e - m.t)}`);
      }
      // sessions as bands
      sessions.forEach((sx, i) => {
        const e = endOf(sx, i); if (e < left || sx.start > right) return;
        const x1 = Math.max(GUTTER, x(sx.start)), x2 = Math.min(W + 2, x(e)); if (x2 - x1 < 1) return;
        const st = sx.status === "done" ? " done" : /blocked|stalled/.test(sx.status || "") ? " blocked" : "";
        const b = el(`tl-band${sx.prep ? " prep" : ""}${st}`, x1, y0 + LANE.marks + 3, x2 - x1 > 46 ? `${sx.prep ? "prep " : ""}${sx.task_id} ${sx.task || ""}` : "",
          { title: `${sx.prep ? "prep · " : ""}${sx.task_id} ${sx.task || ""} · session ${sx.iter} · ${clock(sx.start)}–${sx.end ? clock(e) : "now"}${sx.status ? " · " + sx.status : ""}` });
        b.style.cssText += `;width:${x2 - x1}px;${sty}`;
        b.addEventListener("click", () => { TL.open = { type: "sess", ch: n, iter: sx.iter }; openDrawer(); });
      });
      // file changes, packed into rows; edges join a session's changes in order
      const nodes = [...f.nodes.values()].filter(nd => nd.kind !== "Test" && nd.t >= left - 4000 * TL.spp && nd.t <= right).sort((a, b) => a.t - b.t);
      const rowEnd = Array(LANE.rows).fill(-1e9), placed = new Map();
      let over = null;   // changes with no free row: counted into one "+N" (click zooms in there)
      const flush = () => { if (!over) return; const o = over; over = null;
        const e2 = el("tl-more v-dial", o.x, y0 + LANE.head + LANE.rows * LANE.row + 1, `+${o.n}`, { title: `${o.n} more changes here: click to zoom in` });
        e2.addEventListener("click", ev => { ev.stopPropagation(); zoomBy(0.25, o.x); }); };
      for (const nd of nodes) {
        const xx = x(nd.t), text = base(nd.path), w = Math.min(190, 40 + 6.2 * text.length + 7 * String(nd.add ?? "").length);
        const r = rowEnd.findIndex(end => end + 6 <= xx);
        if (r < 0) { if (xx >= GUTTER) { if (over && xx - over.x < 40) over.n++; else { flush(); over = { x: xx, n: 1 }; } } continue; }
        rowEnd[r] = xx + w;
        if (xx + w < GUTTER) continue;   // off the left edge: it only takes its row
        flush();
        const yy = y0 + LANE.head + r * LANE.row + 2;
        placed.set(nd.id, { x: xx, y: yy + 11, w });
        const k = nd.kind === "Write" ? "k-write" : "k-edit";
        const node = el(`tl-node${TL.open?.id === nd.id ? " sel" : ""}`, xx, yy, [chip(k, nd.kind === "Write" ? "W" : "E"), h("span", { class: "nm" }, text),
          h("span", { class: "plus" }, `+${nd.add ?? 0}`), h("span", { class: "minus" }, `−${nd.del ?? 0}`)],
          { title: `${nd.path} · ${nd.approx ? "≈ " : ""}${clock(nd.t)} · session ${nd.iter}` });
        node.style.maxWidth = `${w}px`;
        node.addEventListener("click", ev => { ev.stopPropagation(); TL.open = { type: "node", ch: n, id: nd.id }; openDrawer(); schedule(); });
      }
      flush();
      let prev = null;
      for (const nd of nodes) { const p = placed.get(nd.id); if (!p) { prev = null; continue; }
        if (prev && prev.iter === nd.iter) { const a = prev.p, bx = p.x, mx = (a.x + a.w + bx) / 2;
          svg.append(s("path", { d: `M${a.x + a.w},${a.y} C${mx},${a.y} ${mx},${p.y} ${bx},${p.y}`, style: "fill: none; stroke: var(--line-2); stroke-width: 1.3" })); }
        prev = { iter: nd.iter, p }; }
      // test runs
      const yt = y0 + LANE.head + LANE.rows * LANE.row + LANE.more + 1;
      const tests = [...f.nodes.values()].filter(q => q.kind === "Test" && q.t >= left && q.t <= right).sort((a, b) => b.t - a.t)
        .map(nd => ({ nd, x: x(nd.t), w: textW(nd.fail || nd.pass, 7) + 8, important: !!nd.fail })).filter(it => it.x >= GUTTER);
      for (const { nd, x: xx } of packRow(tests))
        el(`tl-test v-dial ${nd.fail ? "f" : "p"}`, xx, yt, nd.fail ? `✗ ${nd.fail}` : `✓ ${nd.pass}`, { title: `${nd.cmd || "tests"} · ${nd.pass} passed, ${nd.fail} failed · ${nd.approx ? "≈ " : ""}${clock(nd.t)}` });
      // the driver's marks; merges arc up into main
      const laneMarks = [];
      for (const m of f.marks.values()) {
        if (!MARKS[m.kind] || m.t < left || m.t > right) continue;
        const xx = x(m.t); if (xx < GUTTER) continue;
        if (m.kind === "merged") {
          svg.append(s("path", { d: `M${xx},${y0 + LANE.marks + 3} C${xx},${y0 - 18} ${xx},${mid + 18} ${xx},${mid + 6}`, style: "fill: none; stroke: var(--good); stroke-width: 1.6; opacity: 0.8" }));
          merges.push({ m, x: xx - 4, w: textW(`⇡ ${m.task_id}`, 6.6), sty });
        } else { const txt = `${MARKS[m.kind][1]}${m.task_id ? " " + m.task_id : ""}`;
          laneMarks.push({ m, txt, x: xx, w: textW(txt, 6.4), important: ["stalled", "timeout", "handed"].includes(m.kind) }); }
      }
      for (const { m, txt, x: xx } of packRow(laneMarks.sort((a, b) => b.m.t - a.m.t)))
        el(`tl-mark ${MARKS[m.kind][0]} v-dial`, xx, y0 + 2, txt, { title: `${clock(m.t)} · ${m.text}` });
      const g = el("tl-gutter", 0, y0, [h("span", { class: "tag", style: `background: var(--ch-${colorKey(n) || "x"}, var(--fg-2))` }, letter(n)),
        h("span", { class: "tl-gname" }, label(n)), stateChip(n)], { style: sty });
      g.style.height = `${laneH}px`;
    });
    for (const { m, x: xx, sty } of packRow(merges.sort((a, b) => b.m.t - a.m.t)))
      el("tl-mark tl-merged v-dial", xx, mid - 9, `⇡ ${m.task_id}`, { title: `${clock(m.t)} · ${m.text}`, style: sty });
    const nx = x(nowS());
    if (nx >= GUTTER && nx <= W) { svg.append(s("line", { x1: nx, x2: nx, y1: AXIS_H - 6, y2: H, style: "stroke: var(--k-driver); stroke-width: 1.5; stroke-dasharray: 3 3" }));
      el("tl-now v-dial", nx - 14, 5, "now"); }
  }
  function openDrawer() {
    const o = TL.open; if (!o) { drawer.hidden = true; return; }
    const f = S.flow[o.ch], close = h("button", { class: "icon-btn", "aria-label": "Close", onclick: () => { TL.open = null; drawer.hidden = true; schedule(); } }, "✕");
    drawer.hidden = false;
    if (o.type === "sess") {
      const sx = f?.sessions.get(o.iter); if (!sx) { drawer.hidden = true; return; }
      const ch = [...f.nodes.values()].filter(nd => nd.iter === sx.iter).sort((a, b) => a.t - b.t);
      drawer.replaceChildren(h("div", { class: "tl-dh" }, sx.prep ? chip("k-prep", "prep") : null, h("b", {}, `${sx.task_id} ${sx.task || ""}`), close),
        h("p", { class: "v-dial muted" }, `${label(o.ch)} · session ${sx.iter} · ${clock(sx.start)}–${sx.end ? clock(sx.end) : "now"}${sx.end ? ` (${dur(sx.end - sx.start)})` : ""}`),
        h("dl", { class: "kv v-dial" }, ...[["outcome", sx.status], ["verify", sx.verify], ["hand-over", sx.wrapup]].filter(r => r[1]).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))])),
        h("ul", { class: "tl-list" }, ch.map(nd => h("li", { onclick: () => { if (nd.kind !== "Test") { TL.open = { type: "node", ch: o.ch, id: nd.id }; openDrawer(); schedule(); } } },
          nd.kind === "Test" ? chip(nd.fail ? "k-ask" : "k-say", nd.fail ? `✗ ${nd.fail}` : `✓ ${nd.pass}`) : chip(nd.kind === "Write" ? "k-write" : "k-edit", nd.kind),
          h("span", { class: "v-code" }, nd.kind === "Test" ? nd.cmd || "tests" : nd.path), h("span", { class: "v-dial muted" }, `${nd.approx ? "≈" : ""}${clock(nd.t)}`)))));
      return;
    }
    const nd = f?.nodes.get(o.id); if (!nd) { drawer.hidden = true; return; }
    const sx = f.sessions.get(nd.iter), bodyHost = h("div", {}, h("p", { class: "muted" }, "Loading the diff…"));
    const same = [...f.nodes.values()].filter(q => q.path === nd.path && q.id !== nd.id).sort((a, b) => b.t - a.t).slice(0, 12);
    drawer.replaceChildren(h("div", { class: "tl-dh" }, chip(nd.kind === "Write" ? "k-write" : "k-edit", nd.kind), h("b", { class: "v-code", title: nd.path }, base(nd.path)), close),
      h("p", { class: "v-code muted tl-path" }, nd.path),
      h("p", { class: "v-dial" }, h("span", { class: "plus" }, `+${nd.add ?? 0}`), " ", h("span", { class: "minus" }, `−${nd.del ?? 0}`),
        h("span", { class: "muted" }, ` · ${label(o.ch)} · ${nd.approx ? "≈ " : ""}${clock(nd.t)} · session ${nd.iter}${sx ? ` · ${sx.task_id} ${sx.task || ""}` : ""}`)),
      bodyHost,
      same.length ? h("section", {}, h("span", { class: "label" }, "other changes to this file"), h("ul", { class: "tl-list" }, same.map(q => h("li", { onclick: () => { TL.open = { type: "node", ch: o.ch, id: q.id }; openDrawer(); schedule(); } },
        chip(q.kind === "Write" ? "k-write" : "k-edit", q.kind), h("span", { class: "v-dial" }, `+${q.add} −${q.del}`), h("span", { class: "v-dial muted" }, `${q.approx ? "≈" : ""}${clock(q.t)} · s${q.iter}`))))) : null);
    fetch(`/api/flow/body?ch=${encodeURIComponent(o.ch)}&id=${encodeURIComponent(nd.id)}`).then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(j => { if (TL.open?.id === nd.id) bodyHost.replaceChildren(body(j.body) || h("p", { class: "muted" }, "Nothing to show.")); })
      .catch(() => { if (TL.open?.id === nd.id) bodyHost.replaceChildren(h("p", { class: "muted" }, "The diff is not on the server (it keeps the sprint's changes since it last started).")); });
  }
  // panning and zooming
  let drag = null;
  stage.addEventListener("pointerdown", e => { if (e.target.closest(".tl-node, .tl-band, .tl-more, .tl-gutter, .tl-drawer")) return;
    drag = { x: e.clientX, r: rightT() }; stage.setPointerCapture(e.pointerId); stage.classList.add("dragging"); });
  stage.addEventListener("pointermove", e => { if (!drag) return; setView(TL.spp, drag.r - (e.clientX - drag.x) * TL.spp, false); });
  stage.addEventListener("pointerup", () => { drag = null; stage.classList.remove("dragging"); if (TL.right != null && TL.right >= nowS()) TL.follow = true; });
  stage.addEventListener("wheel", e => {
    const r = stage.getBoundingClientRect(), cx = e.clientX - r.left;
    if (e.ctrlKey || e.metaKey || (Math.abs(e.deltaY) > Math.abs(e.deltaX) && !e.shiftKey && stage.scrollHeight <= stage.clientHeight + 2)) {
      e.preventDefault(); zoomBy(Math.exp(e.deltaY * (e.ctrlKey ? 0.012 : 0.002)), cx);
    } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.shiftKey) {
      e.preventDefault(); const d = e.deltaX || e.deltaY; setView(TL.spp, rightT() + d * TL.spp, false);
    }
  }, { passive: false });
  followBtn.addEventListener("click", () => setView(TL.spp, null, !TL.follow));
  const toolbar = h("div", { class: "tl-bar" },
    projects.length > 1 ? projects.map(p => h("button", { class: "act" + (p === TL.project ? " on" : ""), onclick: () => { TL.project = p; go("flow"); } }, p)) : h("b", { class: "v-name tl-proj" }, TL.project),
    h("div", { class: "row" },
      h("button", { class: "act", onclick: () => zoomBy(1 / 1.6), title: "Zoom in" }, "+"), h("button", { class: "act", onclick: () => zoomBy(1.6), title: "Zoom out" }, "−"),
      h("button", { class: "act", onclick: () => setView(3600 / Math.max(200, stage.clientWidth - GUTTER - 40), null, true) }, "1 h"),
      h("button", { class: "act", onclick: () => setView(6 * 3600 / Math.max(200, stage.clientWidth - GUTTER - 40), null, true) }, "6 h"),
      h("button", { class: "act", onclick: fit }, "Whole sprint"), followBtn),
    h("div", { class: "tl-legend" }, chip("k-edit", "edit"), chip("k-write", "write"), h("span", { class: "tl-test p v-dial" }, "✓ tests"), h("span", { class: "tl-test f v-dial" }, "✗ failing"),
      h("span", { class: "tl-mark tl-merged v-dial" }, "⇡ merged"), h("span", { class: "tl-mark tl-handed v-dial" }, "handed over"), h("span", { class: "tl-hatch-key" }, "waiting")),
    status);
  SUBS.add((ch, kind) => { if (kind === "flow" || kind === "meta" || kind === "add") { schedule(); if (kind === "flow" && TL.open && TL.open.ch === ch && TL.open.type === "sess") openDrawer(); } });
  TICKERS.add(() => { if (TL.follow) schedule(); });
  requestAnimationFrame(() => { draw(); if (TL.open) openDrawer(); });
  return h("div", { class: "tl" }, toolbar, h("div", { class: "tl-body" }, stage, drawer));
}
let tlResize; window.addEventListener("resize", () => { clearTimeout(tlResize); tlResize = setTimeout(() => { if (route === "flow") notify(null, "flow"); }, 120); });

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
  const conn = h("span", { class: "conn v-dial" }), stateHost = h("span", { class: "ch-state" });
  const reBtn = h("button", { class: "act", title: "Drop this stream and reconnect (R)" }, reloadIcon(), "Reconnect");
  const paintConn = () => {
    const c = S.channels[name]; if (!c) return;
    const st = stale(name), age = nowS() - (c.last || 0);
    stateHost.replaceChildren(...[stateChip(name), c.meta.prep && c.meta.running ? chip("k-prep", "prep") : null].filter(Boolean));
    conn.className = "conn v-dial" + (st ? " stale" : "");
    reBtn.className = "act" + (st ? " attn" : "");
    conn.replaceChildren(h("span", { class: "led " + (st ? "warn" : c.meta.running ? "on" : "") }),
      st ? `No updates for ${dur(age)} while the model is working. The stream may be stuck.`
         : `${S.online ? "live" : "offline"} · last update ${dur(age)} ago · ${c.meta.running ? (c.meta.activity || "") : (c.meta.loop ? "between sessions" : "loop stopped")}`);
  };
  reBtn.addEventListener("click", () => reconnect(name, reBtn));
  agentPage.reconnect = () => reconnect(name, reBtn);
  paintConn(); TICKERS.add(paintConn);
  SUBS.add((ch, kind) => { if (ch === name && kind === "meta") paintConn(); });
  const side = h("aside", { class: "side" }, ...taskPanel(name));
  SUBS.add((ch, kind) => { if (ch === name && (kind === "task" || (kind === "meta" && side.dataset.task !== S.channels[name]?.meta.task_id))) {
    side.dataset.task = S.channels[name]?.meta.task_id || ""; side.replaceChildren(...taskPanel(name).filter(Boolean)); } });
  side.dataset.task = S.channels[name].meta.task_id || "";
  const ta = h("textarea", { id: `msg-${name}`, rows: 2, placeholder: `Message ${label(name)}…`, maxlength: 4000 });
  const send = stop => { const t = ta.value.trim(); if (!t) return ta.focus(); sendMsg(name, t, stop, f); ta.value = ""; };
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(false); } });
  return h("div", { class: "agent", style: chStyle(name) },
    h("div", { class: "agent-main" },
      h("div", { class: "agent-bar" }, stateHost, conn, reBtn), sc,
      h("div", { class: "composer" }, ta, h("div", { class: "row" }, h("small", {}, "Enter sends · read after its current step · Shift+Enter for a new line"),
        h("div", { class: "row" }, h("button", { class: "act danger", onclick: () => send(true), title: "Ends the running session now; the next one starts with your message" }, "Stop session & send"),
          h("button", { class: "act primary", onclick: () => send(false) }, "Send"))))),
    side);
}

/* ================================================================ routing, ticking */
function go(id, keepScroll) {
  if (!["live", "board", "hardware", "flow"].includes(id) && !S.channels[id] && Object.keys(S.channels).length) id = "live";
  route = id; SUBS.clear(); TICKERS.clear();
  const ch = S.channels[id];
  $("#title").textContent = { live: "Live", board: "Board", hardware: "Hardware", flow: "Timeline" }[id] || (ch ? `${label(id)} · ${ch.meta.project || ""}` : id);
  $("#view").replaceChildren(id === "live" ? live() : id === "board" ? board() : id === "hardware" ? hardware() : id === "flow" ? timeline() : agentPage(id));
  $("#main").dataset.route = ["live", "flow"].includes(id) ? "live" : ["board", "hardware"].includes(id) ? "page" : "agent";
  if (!keepScroll) $("#main").scrollTop = 0;
  renderChrome();
  store.set("lab-console-route", id);
  if (decodeURIComponent(location.hash.slice(1)) !== id) history.pushState(null, "", "#" + encodeURIComponent(id));
}
const sunIcon = () => icon(["M8 4.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7", "M8 1v1.5", "M8 13.5V15", "M1 8h1.5", "M13.5 8H15", "M3 3l1 1", "M12 12l1 1", "M3 13l1-1", "M12 4l1-1"]);
const moonIcon = () => icon(["M13.5 9.5A5.5 5.5 0 1 1 6.5 2.5a4.5 4.5 0 0 0 7 7z"]);
function paintTheme() {
  const dark = document.documentElement.dataset.theme === "dark";
  $("#theme").replaceChildren(dark ? sunIcon() : moonIcon());
  $("#theme").title = dark ? "Switch to light" : "Switch to dark";
}
$("#theme").addEventListener("click", () => {
  const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t; store.set("lab-console-theme", t); paintTheme();
  if (route === "hardware") notify(null, "hist");   // the charts read colours when drawn
});
paintTheme();
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
