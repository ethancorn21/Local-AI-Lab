#!/usr/bin/env python3
"""End-to-end test of the lab console: labdash-ship -> labdash-ingest -> labdash -> a browser's event stream, and the
operator's messages and answers back down the same path into the agent's files. Runs locally against a fake projects
tree (one team project with a worktree for agent a, one hidden project); no AI box, no VM, no SSH.

A real session file is fed into the worktree a piece at a time, as Pi writes it, with an operator message and a
reply containing HTML spliced in. Checks: the stream a browser gets carries every tool call, all the thinking and
answer text, the driver's loop.log lines, context size, the task panel and the sprint board; a second session starts
cleanly; messages land in .agent/inbox, answers in .agent/asks and the open request leaves the panel; a resync replays
the channel; the hidden project never reaches the console; the server refuses other Host headers, cross-site POSTs,
oversized bodies and paths outside its file list; a dropped VM link shows and refuses commands. A second agent's
session holds a pytest summary with no "passed" and a line the shipper cannot read: each costs only itself, noted in
that agent's feed. A shipper restart replays each pane in place, never wiping the page, and drops an agent that
stopped meanwhile; ingest exits cleanly when the server goes away. Then the page itself
(jsdom): every view renders from the captured state with no script errors, live updates land, agent text with HTML in
it stays text.

Usage: python3 test_e2e.py <session.jsonl>    any real .agent/sessions/iter-*.jsonl
       The page checks need jsdom (npm i jsdom in any dir, then NODE_PATH=<dir>/node_modules); skipped without it.
"""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.dirname(HERE)
REPO = os.path.dirname(DASH)
SHIP, INGEST, SERVER = f"{DASH}/ship/labdash-ship", f"{DASH}/server/labdash-ingest", f"{DASH}/server/labdash"
SPRINT = f"{REPO}/harness/tools/sprint-progress"
PY = sys.executable
SESS = open(sys.argv[1], "rb").read().splitlines(keepends=True)
S = tempfile.mkdtemp(prefix="labdash-e2e-")
P = f"{S}/projects"
MAIN, WT, WT_B, SECRET = f"{P}/demo", f"{P}/demo.a", f"{P}/demo.b", f"{P}/secret"
FAILS = []
CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")   # as in labdash-ship


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"   [{detail}]"), flush=True)
    if not ok:
        FAILS.append(name)


def write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as fh:
        fh.write(text)


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------------------------------------ the fake projects

TASK_002 = """Status: in-progress
# 002: Feed rows
Depends on: 001
Touches: web/feed.py, web/static/feed.css

## Goal
Each feed row shows its source and age.

## Acceptance
- [x] rows carry the source name
- [ ] rows carry the age,
      relative to now
- [ ] tests pass
"""
for d in (MAIN, WT, WT_B, SECRET):
    os.makedirs(f"{d}/.agent/sessions")
write(f"{MAIN}/.agent/team-main", "")
write(f"{MAIN}/tasks/001-scaffold.md", "Status: done\n# 001: Scaffold\n")
write(f"{MAIN}/tasks/002-feed-rows.md", TASK_002.replace("[x]", "[ ]"))
write(f"{MAIN}/tasks/003-theme-page.md", "Status: open\n# 003: Theme page\nDepends on: 002\n")
write(f"{MAIN}/tasks/004-docs.md", "Status: open\n# 004: Docs\n")
write(f"{MAIN}/tasks/prep/002.md", "Prep: the feed module already has a row template.\n")
write(f"{MAIN}/.agent/team/claims/002/owner", f"a 1 0 {WT}/tasks/002-feed-rows.md\n")
write(f"{WT}/.agent/team.env", f"TEAM_DIR={MAIN}\nAGENT_ID=a\nLLM_URL=http://127.0.0.1:8080/v1\n")
write(f"{WT}/tasks/002-feed-rows.md", TASK_002)
write(f"{WT}/PROGRESS.md", "Working on the age column.\n")
write(f"{WT}/.agent/iterations.jsonl", json.dumps({"iter": 1, "task": "tasks/002-feed-rows.md", "start": "2026-10-07T01:00:00",
                                                    "end": "2026-10-07T01:20:00", "status_after": "in-progress"}) + "\n")
write(f"{WT}/.agent/asks/001.md", "# Request 001\nstatus: open\nblocking: yes\ntask: tasks/002-feed-rows.md\n"
                                  "asked: 2026-10-07 01:00 UTC, iteration 1\n\n## Request\nRelative or absolute ages?\n")
write(f"{WT}/.agent/asks/002.md", "# Request 002\nstatus: closed\nblocking: no\n\n## Request\nOld one.\n")
write(f"{SECRET}/.agent/sessions/iter-0001.jsonl", json.dumps({"type": "message_update", "assistantMessageEvent":
                                                               {"type": "text_delta", "delta": "SECRET-MARKER"}}) + "\n")
write(f"{SECRET}/.agent/loop.log", f"{stamp()} iteration 1: tasks/001-x.md (harness 0)\n")
write(f"{S}/hidden", "# one per line\nsecret\n")

sleepers = [subprocess.Popen(["sleep", "3000"]) for _ in range(3)]
write(f"{WT}/.agent/loop.pid", str(sleepers[0].pid))
write(f"{WT}/.agent/pi.pid", str(sleepers[1].pid))
write(f"{SECRET}/.agent/loop.pid", str(sleepers[2].pid))
write(f"{WT}/.agent/loop.log", f"{stamp()} iteration 1: tasks/002-feed-rows.md (harness abc)\n")
sess1 = open(f"{WT}/.agent/sessions/iter-0001.jsonl", "wb", buffering=0)


def ev_line(o):
    return (json.dumps(o) + "\n").encode()


# Agent b: no loop running (on the console because its session is recent). Its session holds what once took every
# agent off the console: a pytest summary with failures and no "passed", and a line the shipper cannot read.
def tool_pair(i, name, args, text):
    return [ev_line({"type": "tool_execution_start", "toolCallId": f"t{i}", "toolName": name, "args": args}),
            ev_line({"type": "tool_execution_end", "toolCallId": f"t{i}", "toolName": name, "isError": False,
                     "result": {"content": [{"type": "text", "text": text}]}})]


write(f"{WT_B}/.agent/team.env", f"TEAM_DIR={MAIN}\nAGENT_ID=b\nLLM_URL=http://127.0.0.1:8081/v1\n")
SESS_B = b"".join(
    tool_pair(1, "bash", {"command": "pytest -q"}, "FAILED tests/test_rows.py::test_one - Assertio...\n2 failed, 4 warnings in 11.50s")
    + [ev_line({"type": "tool_execution_start", "toolCallId": "t2", "toolName": "bash", "args": ["not", "a", "dict"]})]
    + tool_pair(3, "read", {"path": "web/feed.py", "limit": "forty"}, "line one\nline two")
    + [ev_line({"type": "message_update", "assistantMessageEvent": {"type": "text_start"}}),
       ev_line({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "after the bad line"}}),
       ev_line({"type": "message_update", "assistantMessageEvent": {"type": "text_end"}})])
write(f"{WT_B}/.agent/sessions/iter-0001.jsonl", SESS_B.decode())

HTML_REPLY = '<img src=x onerror="window.PWNED=1"><b>bold?</b>'
SPLICE = [ev_line({"type": "message_end", "message": {"role": "user", "content": [
              {"type": "text", "text": "[Message from the human, typed while you were working]\nhello from the test"}]}}),
          ev_line({"type": "message_update", "assistantMessageEvent": {"type": "text_start"}}),
          ev_line({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": HTML_REPLY}}),
          ev_line({"type": "message_update", "assistantMessageEvent": {"type": "text_end"}})]
cut = int(len(SESS) * 0.4)
FED = SESS[:cut] + SPLICE + SESS[cut:]


def expected(lines):
    """What the stream must carry: tool count, all thinking and answer text (whitespace ignored)."""
    tools, think, say = 0, [], []
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if e.get("type") == "tool_execution_start":
            tools += 1
        d = e.get("assistantMessageEvent") or {}
        if d.get("type") == "thinking_delta":
            think.append(d.get("delta", ""))
        elif d.get("type") == "text_delta":
            say.append(d.get("delta", ""))
    squash = lambda parts: "".join(CTRL.sub("", "".join(parts)).split())   # the shipper drops control characters
    return tools, squash(think), squash(say)


# ------------------------------------------------------------------------------------------------ server, stream, link

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


PORT = free_port()
HOST = f"localhost:{PORT}"
SOCK = f"{S}/ingest.sock"
write(f"{S}/config.json", json.dumps({"listen": ["127.0.0.1", PORT], "hosts": [HOST], "socket": SOCK, "web": f"{DASH}/web",
                                      "hwtemps": f"{S}/none.csv", "servers": {}}))
server = subprocess.Popen([PY, SERVER, f"{S}/config.json"], stderr=open(f"{S}/server.log", "w"))
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.2).close()
        if os.path.exists(SOCK):
            break
    except OSError:
        time.sleep(0.1)

MSGS, RAW, LOCK = [], [], threading.Lock()


def stream_reader():
    c = socket.create_connection(("127.0.0.1", PORT))
    c.sendall(f"GET /api/stream HTTP/1.1\r\nHost: {HOST}\r\nAccept: text/event-stream\r\n\r\n".encode())
    for line in c.makefile("rb"):
        with LOCK:
            RAW.append(line)
        if line.startswith(b"data: "):
            with LOCK:
                MSGS.append(json.loads(line[6:]))


threading.Thread(target=stream_reader, daemon=True).start()


def msgs(**want):
    with LOCK:
        return [m for m in MSGS if all(m.get(k) == v for k, v in want.items())]


def wait_for(pred, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        r = pred()
        if r:
            return r
        time.sleep(0.1)
    return pred()


def http(method, path, body=None, headers=None):
    h = {"Host": HOST, **(headers or {})}
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def post(path, body, **extra):
    return http("POST", path, body, {"X-Labdash": "1", "Origin": f"http://{HOST}", **extra})


def state():
    return json.loads(http("GET", "/api/state")[2])


env = {**os.environ, "LABDASH_PROJECTS": P, "LABDASH_HIDDEN": f"{S}/hidden", "LABDASH_SPRINT": SPRINT, "LABDASH_SOCKET": SOCK}
ship = subprocess.Popen([PY, SHIP, "--stdio"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(f"{S}/ship.log", "w"))
ingest = subprocess.Popen([PY, INGEST], env=env, stdin=ship.stdout, stdout=ship.stdin)

try:
    # ---- the first session, streamed in pieces
    check("feed connects", wait_for(lambda: state()["feed"]["connected"]))
    chunks = 12
    for i in range(chunks):
        part = b"".join(FED[i * len(FED) // chunks:(i + 1) * len(FED) // chunks])
        half = len(part) // 2   # a line split mid-write, as big writes land
        sess1.write(part[:half])
        time.sleep(0.15)
        sess1.write(part[half:])
        time.sleep(0.25)
        if i == chunks // 2:
            write(f"{WT}/.agent/loop.log", f"{stamp()} merged 002 into main (verified)\n", "a")
    want_tools, want_think, want_say = expected(FED)

    def settled():
        c = state()["channels"].get("demo.a")
        if not c:
            return None
        tools = [e for e in c["events"] if e["t"] == "tool"]
        return c if len(tools) == want_tools and not any(e.get("running") for e in tools) else None

    c = wait_for(settled, 30) or state()["channels"].get("demo.a") or {"meta": {}, "events": [], "snap": None}
    ev = c["events"]
    check("channel demo.a with its agent, project and port", c["meta"].get("agent") == "a" and c["meta"].get("project") == "demo"
          and c["meta"].get("port") == "8080", c["meta"])
    check("session divider first", ev and ev[0] == {**ev[0], "id": "1-0", "t": "divider"}, ev[:1])
    tools = [e for e in ev if e["t"] == "tool"]
    check(f"every tool call arrives and finishes ({want_tools})", len(tools) == want_tools and all(not e.get("running") for e in tools),
          f"{len(tools)} tools, {sum(1 for e in tools if e.get('running'))} still running")
    check("tool bodies: code, diff or output on each", all(isinstance(e.get("body"), dict) for e in tools),
          [e["target"] for e in tools if not isinstance(e.get("body"), dict)][:3])
    got_think = "".join("".join(e["text"] for e in ev if e["t"] == "think").split())
    got_say = "".join("".join(e["text"] for e in ev if e["t"] == "say").split())
    check("all thinking text, in order", got_think == want_think, f"{len(got_think)} of {len(want_think)} chars")
    check("all answer text, in order", got_say == want_say, f"{len(got_say)} of {len(want_say)} chars")
    check("streamed text came as appends", len(msgs(type="upd")) > len(tools) and any(m.get("append") for m in msgs(type="upd")))
    check("every text block closed", all(e.get("done") for e in ev if e["t"] in ("think", "say")))
    check("operator message shown as you", any(e["t"] == "you" and e["text"] == "hello from the test" for e in ev))
    check("loop.log line as a good driver line", any(e["t"] == "sys" and e.get("good") and "merged 002" in e["text"] for e in ev))
    check("context size in meta", isinstance(c["meta"].get("ctx"), int) and c["meta"]["ctx"] > 1000, c["meta"].get("ctx"))
    check("task and activity in meta", c["meta"].get("task_id") == "002" and c["meta"].get("task") == "feed rows"
          and c["meta"].get("activity") == "session finished", {k: c["meta"].get(k) for k in ("task_id", "task", "activity")})
    snap = wait_for(lambda: state()["channels"]["demo.a"]["snap"])
    t = (snap or {}).get("task") or {}
    check("task panel: title, goal, status, touches", t.get("title") == "Feed rows" and t.get("goal") == "Each feed row shows its source and age."
          and t.get("status") == "in-progress" and t.get("touches") == ["web/feed.py", "web/static/feed.css"], t)
    check("task panel: acceptance boxes, continuation joined", t.get("checks") == [[True, "rows carry the source name"],
          [False, "rows carry the age, relative to now"], [False, "tests pass"]], t.get("checks"))
    check("task panel: depends and unlocks", t.get("depends") == [["001", "done"]] and t.get("unlocks") == [["003", "theme page"]],
          (t.get("depends"), t.get("unlocks")))
    check("task panel: open request only", [a["id"] for a in (snap or {}).get("asks", [])] == ["001"]
          and snap["asks"][0]["text"] == "Relative or absolute ages?", (snap or {}).get("asks"))
    check("task panel: history, PROGRESS, prep", len(snap.get("history", [])) == 1 and "age column" in snap.get("progress", "")
          and "row template" in snap.get("prep", ""))
    b = wait_for(lambda: state()["boards"].get("demo"))
    check("sprint board", b and b["total"] == 4 and [x["id"] for x in b["done"]] == ["001"]
          and [(x["id"], x["agent"], x["boxes"]) for x in b["building"]] == [("002", "a", [1, 3])]
          and [(x["id"], x["waits"]) for x in b["open"]] == [("003", ["002"]), ("004", [])], b)
    cb = wait_for(lambda: (lambda c: c if c and any(e.get("text") == "after the bad line" for e in c["events"]) else None)(state()["channels"].get("demo.b")))
    evb = (cb or {}).get("events", [])
    check("agent b on the console beside a", cb and cb["meta"].get("agent") == "b" and "demo.a" in state()["channels"], list(state()["channels"]))
    check("pytest summary with failures and no 'passed' counted", any(e["t"] == "tool" and e["target"] == "pytest -q"
          and e.get("meta") == {"pass": 0, "fail": 2} for e in evb), [e.get("meta") for e in evb if e["t"] == "tool"])
    check("read with a non-numeric limit shown", any(e["t"] == "tool" and e["kind"] == "Read" and e.get("meta") == "2 lines" for e in evb))
    check("unreadable line skipped and noted in b's feed", [e["text"][:40] for e in evb if e["t"] == "sys"] == ["console: skipped a session event it coul"],
          [e["text"] for e in evb if e["t"] == "sys"])
    check("b's stream goes on after it", bool(cb))
    check("shipper logged the fault once", open(f"{S}/ship.log").read().count("fault in demo.b (a session event): AttributeError") == 1,
          open(f"{S}/ship.log").read()[-400:])
    check("main checkout is not a channel", "demo" not in state()["channels"])
    check("hidden project never shipped", not any(n.startswith("secret") for n in state()["channels"]) and "secret" not in state()["boards"]
          and not any(b"SECRET-MARKER" in r for r in RAW))
    write(f"{S}/state.json", json.dumps(state()))

    # ---- the next session
    write(f"{WT}/.agent/loop.log", f"{stamp()} iteration 2: tasks/002-feed-rows.md (harness abc)\n", "a")
    write(f"{WT}/.agent/sessions/iter-0002.jsonl", b"".join(SESS[:300]).decode(errors="replace"))
    c2 = wait_for(lambda: (lambda c: c if c["meta"].get("session") == 2 and any(e["id"] == "2-0" for e in c["events"]) else None)(state()["channels"]["demo.a"]))
    check("second session: divider and meta", bool(c2))
    check("both sessions kept", c2 and c2["events"][0]["id"] == "1-0")

    # ---- the operator's side
    st, _, body = post("/api/message", {"ch": "demo.a", "text": "please check the timezone"})
    mid = json.loads(body).get("id") if st == 202 else None
    check("message accepted", st == 202 and mid, (st, body))
    check("message acknowledged", wait_for(lambda: msgs(type="ack", id=mid, ok=True)))
    inbox = lambda: sorted(os.listdir(f"{WT}/.agent/inbox")) if os.path.isdir(f"{WT}/.agent/inbox") else []
    check("message in the inbox", [open(f"{WT}/.agent/inbox/{f}").read() for f in inbox()] == ["please check the timezone\n"], inbox())
    st, _, body = post("/api/message", {"ch": "demo.a", "text": "stop and fix", "stop": True})
    wait_for(lambda: msgs(type="ack", id=json.loads(body).get("id"), ok=True))
    check("stop message as .now.md", any(f.endswith(".now.md") for f in inbox()) and not any(f.startswith(".") for f in inbox()), inbox())
    st, _, body = post("/api/answer", {"ch": "demo.a", "ask": "001", "text": "Relative, like 3h."})
    aid = json.loads(body).get("id")
    check("answer acknowledged", st == 202 and wait_for(lambda: msgs(type="ack", id=aid, ok=True)), (st, body))
    ask = open(f"{WT}/.agent/asks/001.md").read()
    check("answer in the request file", "status: answered" in ask and "status: open" not in ask and ask.rstrip().endswith("console)\nRelative, like 3h."), ask[-120:])
    check("answered request leaves the panel", wait_for(lambda: state()["channels"]["demo.a"]["snap"]["asks"] == [], 5))
    st, _, body = post("/api/answer", {"ch": "demo.a", "ask": "001", "text": "again"})
    aid = json.loads(body).get("id")
    check("second answer refused by the shipper", wait_for(lambda: [m for m in msgs(type="ack", id=aid) if not m["ok"] and "not open" in m["error"]]))
    check("bad request number refused", post("/api/answer", {"ch": "demo.a", "ask": "../x", "text": "t"})[0] == 400)
    check("unknown agent refused", post("/api/message", {"ch": "nobody", "text": "t"})[0] == 404)
    check("empty message refused", post("/api/message", {"ch": "demo.a", "text": "  "})[0] == 400)
    n_before = len(state()["channels"]["demo.a"]["events"])
    resets = len(msgs(type="reset", ch="demo.a"))
    check("resync accepted", post("/api/resync", {"ch": "demo.a"})[0] == 202)
    check("resync replays the channel", wait_for(lambda: len(msgs(type="reset", ch="demo.a")) > resets
                                                 and len(state()["channels"]["demo.a"]["events"]) >= min(n_before, 3)))

    # ---- the server's own guards
    st, hd, body = http("GET", "/")
    check("page served with its CSP", st == 200 and "script-src 'self'" in hd.get("Content-Security-Policy", "") and b"/app.js" in body)
    check("other Host refused", http("GET", "/", headers={"Host": "evil.example:80"})[0] == 421)
    check("files outside the list refused", all(http("GET", p)[0] == 404 for p in ("/index.html", "/../labdash", "/app.js/..", "/config.json")))
    check("POST without the header refused", http("POST", "/api/resync", {}, {"Origin": f"http://{HOST}"})[0] == 403)
    check("cross-site POST refused", post("/api/resync", {}, Origin="http://evil.example")[0] == 403)
    check("oversized POST refused", post("/api/message", b"x" * 100000)[0] == 413)

    # ---- the VM link drops
    ingest.kill()
    check("dropped link shown", wait_for(lambda: state()["feed"]["connected"] is False))
    check("commands refused while down", post("/api/message", {"ch": "demo.a", "text": "hi"})[0] == 503)
    check("streams kept while down", "demo.a" in state()["channels"])

    # ---- the shipper comes back (a restart): panes stay put, a loop that stopped meanwhile leaves
    ship.kill()
    ship.wait()
    old = time.time() - 7 * 3600
    os.utime(f"{WT_B}/.agent/sessions/iter-0001.jsonl", (old, old))
    n_snap, n_reset = len(msgs(type="snapshot")), len(msgs(type="reset", ch="demo.a"))
    ship = subprocess.Popen([PY, SHIP, "--stdio"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(f"{S}/ship2.log", "w"))
    ingest = subprocess.Popen([PY, INGEST], env=env, stdin=ship.stdout, stdout=ship.stdin, stderr=open(f"{S}/ingest2.log", "w"))
    check("reconnect: link back", wait_for(lambda: state()["feed"]["connected"]))
    check("reconnect: b, stopped meanwhile, leaves", wait_for(lambda: msgs(type="gone", ch="demo.b") and "demo.b" not in state()["channels"]))
    check("reconnect: a replayed in place, page never wiped", wait_for(lambda: len(msgs(type="reset", ch="demo.a")) > n_reset)
          and len(msgs(type="snapshot")) == n_snap and not msgs(type="gone", ch="demo.a") and "demo.a" in state()["channels"])
    st, _, body = post("/api/message", {"ch": "demo.a", "text": "back again"})
    check("reconnect: commands work", st == 202 and wait_for(lambda: msgs(type="ack", id=json.loads(body).get("id"), ok=True)), (st, body))
    check("reconnect: board kept", "demo" in state()["boards"])

    check("server log clean", "Traceback" not in open(f"{S}/server.log").read(), open(f"{S}/server.log").read()[-400:])
    for lg in ("ship.log", "ship2.log"):
        text = open(f"{S}/{lg}").read()
        check(f"shipper log clean, no faults on the real session ({lg})", "Traceback" not in text and "fault in demo.a" not in text, text[-400:])
    server.terminate()
    try:
        rc = ingest.wait(10)
    except subprocess.TimeoutExpired:
        rc = None
    check("ingest exits cleanly when the server goes away", rc == 0 and "Fatal" not in open(f"{S}/ingest2.log").read(),
          (rc, open(f"{S}/ingest2.log").read()[-300:]))

    # ---- the page
    try:
        r = subprocess.run(["node", f"{HERE}/render.cjs", f"{DASH}/web", f"{S}/state.json"], capture_output=True, text=True, timeout=120)
        out = r.stdout.strip().splitlines()
    except FileNotFoundError:
        r, out = None, []
    if r is None or r.returncode == 3:
        print("SKIP  page checks (no node or no jsdom: npm i jsdom, then NODE_PATH=<dir>/node_modules)")
    else:
        for line in out:
            o = json.loads(line)
            check("page: " + o["name"], o["ok"], o.get("detail", ""))
        if r.returncode not in (0, 1) or not out:
            check("page script ran", False, r.stderr[-600:])
finally:
    for p in [ship, ingest, server, *sleepers]:
        try:
            p.send_signal(signal.SIGTERM)
        except OSError:
            pass
    if not FAILS:
        shutil.rmtree(S, ignore_errors=True)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED (work files kept in {S})'}")
sys.exit(1 if FAILS else 0)
