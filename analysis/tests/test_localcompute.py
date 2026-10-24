#!/usr/bin/env python3
"""The local-compute service end to end, no model and no GPU: lcd.py runs with a stub lc-setup that starts stub
OpenAI servers (one per setup) answering with scripted tool calls. Checked: tokens, the GPU hold while the bench
runs, the idle setup, the OpenAI door (plain and streamed) and its switch to another setup and back, chat jobs, a
swarm job with the 27B's check, the implementation agent (a protected test file, finish refused before the tests
pass, the patch), the sandbox (no /home, no /opt, no network), GrokBot's approval rule and the owner's ping, job
privacy, cancel, a setup that stops on its own (held until resume), and the MCP server over stdio.

Needs bwrap (the AI box has it). usage: python3 analysis/tests/test_localcompute.py [path/to/server/localcompute]
"""
import http.server, io, json, os, socket, subprocess, sys, tarfile, tempfile, threading, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LC = os.path.realpath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "server", "localcompute"))

STUB = r'''
import http.server, json, sys, time
SETUP, PORT = sys.argv[1], int(sys.argv[2])

def calls(*cs):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(cs)]}

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, data, ctype="application/json"):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/health": return self.send(200, b"{}")
        if self.path == "/metrics":
            return self.send(200, b'vllm:cache_config_info{engine="0",kv_cache_size_tokens="400000"} 1.0\n', "text/plain")
        self.send(404, b"{}")
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert b["model"] == SETUP, (b["model"], SETUP)
        ms = b["messages"]; turn = 1 + sum(m["role"] == "assistant" for m in ms)
        text = "\n".join(str(m.get("content") or "") for m in ms)
        if b.get("tools") and "implementation agent" in ms[0]["content"]:
            if "SANDBOXCHECK" in text:
                msg = calls(("run_tests", {})) if turn == 1 else calls(("finish", {"summary": "checked"}))
            elif turn == 1:
                msg = calls(("read_file", {"path": "calc.py"}))
            elif turn == 2:   # a protected file, and finish before any test passed: both refused
                msg = calls(("write_file", {"path": "tests/test_calc.py", "content": "x"}), ("finish", {"summary": "early"}))
            elif turn == 3:
                msg = calls(("edit_file", {"path": "calc.py", "old": "return a - b", "new": "return a + b"}), ("run_tests", {}))
            else:
                msg = calls(("finish", {"summary": "add fixed"}))
        elif b.get("tools"):   # a scout
            path = text.split("YOUR AREA thoroughly:\n")[1].splitlines()[0].lstrip("- ").split(" lines ")[0]
            msg = (calls(("read_file", {"path": path, "start": 1, "lines": 150})) if turn == 1 else
                   calls(("report", {"path": path, "line": 2, "finding": "wrong operator"}),
                         ("report", {"path": path, "line": 7, "finding": "maybe off by one"}), ("finish", {"summary": "done"})))
        elif "Answer with BUG or NOT A BUG" in text:
            msg = {"role": "assistant", "content": "BUG\nit subtracts" if "at line 2:" in text else "NOT A BUG\nfine"}
        else:
            time.sleep(0.2)
            msg = {"role": "assistant", "content": f"echo {SETUP}"}
        if b.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for piece in ("echo ", SETUP):
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode()); self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n"); return
        self.send(200, json.dumps({"choices": [{"message": msg, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 1000 * turn, "completion_tokens": 30}}).encode())

http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
'''

SETUP_SH = r'''#!/usr/bin/env bash
D=$(dirname "$0")
case $1 in
  start) "$0" stop; python3 "$D/stub.py" "$2" "$3" >/dev/null 2>&1 & echo "$! $2" > "$D/running"
         for _ in $(seq 50); do curl -sf "http://127.0.0.1:$3/health" >/dev/null && exit 0; sleep 0.1; done; exit 1 ;;
  stop) [ -f "$D/running" ] && kill "$(cut -d' ' -f1 "$D/running")" 2>/dev/null; rm -f "$D/running"; exit 0 ;;
  running) [ -f "$D/running" ] && kill -0 "$(cut -d' ' -f1 "$D/running")" 2>/dev/null && cut -d' ' -f2 "$D/running"; exit 0 ;;
  gpu-busy) [ -f "$D/gpu-busy" ] ;;
esac
'''


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def targz(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in files.items():
            data = text.encode(); ti = tarfile.TarInfo(name); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def main():
    ok, failed = 0, []

    def check(cond, what):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + what, flush=True)
        if cond:
            ok += 1
        else:
            failed.append(what)

    with tempfile.TemporaryDirectory() as d:
        stubd = os.path.join(d, "stub"); os.makedirs(stubd)
        open(os.path.join(stubd, "stub.py"), "w").write(STUB)
        setup = os.path.join(stubd, "lc-setup"); open(setup, "w").write(SETUP_SH); os.chmod(setup, 0o755)
        open(os.path.join(stubd, "gpu-busy"), "w").close()   # the bench holds the GPUs at first
        port, pings = free_port(), os.path.join(d, "pings")
        ports = {s: free_port() for s in ("main", "swarm", "big")}
        env = {**os.environ, "LC_STATE": os.path.join(d, "state"), "LC_TOKENS": os.path.join(d, "tokens.json"),
               "LC_SETUP": setup, "LC_PORTS": str(port), "LC_SETUP_PORTS": ",".join(f"{k}={v}" for k, v in ports.items()),
               "LC_CHECK_EVERY": "1", "LC_IDLE_RETURN": "3", "LC_MAX_WAIT": "5", "LC_PING": f"tee -a {pings}",
               "LC_SANDBOX_SCOPE": os.environ.get("LC_SANDBOX_SCOPE", "0")}
        lcd = os.path.join(LC, "lcd.py")
        tok = {n: subprocess.run([sys.executable, lcd, "token", "add", n, *extra], env=env, capture_output=True, text=True,
                                 check=True).stdout.strip()
               for n, extra in (("claude", []), ("grok", ["--approve", "swarm,implement"]), ("owner", ["--admin"]))}
        proc = subprocess.Popen([sys.executable, lcd, "serve"], env=env, stderr=open(os.path.join(d, "lcd.log"), "w"))
        base = f"http://127.0.0.1:{port}"

        def call(method, path, body=None, who="claude", raw=None, timeout=60):
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            h = {"Content-Type": "application/json"}
            if who:
                h["Authorization"] = f"Bearer {tok.get(who, who)}"
            req = urllib.request.Request(base + path, data=data, method=method, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    body = r.read()
                    return r.status, (json.loads(body) if body.startswith(b"{") else body.decode())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        def until(cond, secs=20):
            end = time.time() + secs
            while time.time() < end:
                try:
                    if cond():
                        return True
                except Exception:
                    pass
                time.sleep(0.2)
            return False

        def status():
            return call("GET", "/status")[1]

        try:
            check(until(lambda: call("GET", "/health", who=None)[0] == 200), "the daemon answers /health without a token")
            check(call("GET", "/status", who=None)[0] == 401 and call("GET", "/status", who="lc_wrong")[0] == 401,
                  "no token or a wrong one: 401")
            time.sleep(2.5)
            st = status()
            check(st["loaded"] is None and st["gpu_busy_with_bench"], f"while the bench holds the GPUs nothing loads: {st['loaded']}")
            os.remove(os.path.join(stubd, "gpu-busy"))
            check(until(lambda: status()["loaded"] == "main"), "once the bench is gone, the idle setup (main) loads")

            code, r = call("POST", "/v1/chat/completions", {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]})
            check(code == 200 and r["choices"][0]["message"]["content"] == "echo main", f"OpenAI door, unknown model -> main: {code}")
            code, r = call("POST", "/v1/chat/completions", {"model": "main", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
            check(code == 200 and "data: [DONE]" in r and '"echo "' in r, "a streamed answer passes through")
            code, r = call("POST", "/v1/chat/completions", {"model": "big", "messages": [{"role": "user", "content": "hi"}]})
            check(code == 200 and r["choices"][0]["message"]["content"] == "echo big", "model big: the service switches to big and answers")
            check(until(lambda: status()["loaded"] == "main", 15), "idle on big past LC_IDLE_RETURN: back to main")
            code, r = call("GET", "/v1/models")
            check([m["id"] for m in r["data"]] == ["main", "swarm", "big"], "models list the setups")

            code, j = call("POST", "/jobs", {"kind": "chat", "messages": [{"role": "user", "content": "hello"}], "thinking": False})
            code, j = call("GET", f"/jobs/{j['id']}?wait=20")
            check(j["state"] == "done" and j["result"]["content"] == "echo main", f"a chat job: {j['state']}")

            proj = {"pkg/a.py": "".join(f"x{i} = {i}\n" for i in range(30)), "pkg/b.py": "".join(f"y{i} = {i}\n" for i in range(30)),
                    "README.md": "not python\n"}
            code, up = call("POST", "/uploads", raw=targz(proj))
            check(code == 200 and up["files"] == 3, f"an upload: {up}")
            code, j = call("POST", "/jobs", {"kind": "swarm", "upload": up["upload"], "areas": 2, "verify": True})
            check(code == 200 and j["state"] == "queued", "a swarm job with the check queues")
            code, j = call("GET", f"/jobs/{j['id']}?wait=60", timeout=90)
            r = j.get("result") or {}
            check(j["state"] == "done" and r.get("verified") is True and r.get("areas") == 2 and r.get("files") == 2,
                  f"the swarm ran on swarm, then main checked it: {j['state']} {j.get('error')}")
            fs = r.get("findings", [])
            check(len(fs) == 4 and r.get("kept") == 2 and all(f["verdict"] == ("yes" if f["line"] == 2 else "no") for f in fs),
                  f"findings clustered and judged: {[(f['path'], f['line'], f.get('verdict')) for f in fs]}")
            sw = [(s["from"], s["to"]) for s in status()["switches"]]
            check(("main", "swarm") in sw and ("swarm", "main") in sw, f"switches main -> swarm -> main: {sw}")
            code, up2 = call("POST", "/uploads", raw=targz(proj))
            code, j2 = call("POST", "/jobs", {"kind": "swarm", "upload": up2["upload"], "glob": "*.md"})
            code, j2 = call("GET", f"/jobs/{j2['id']}?wait=60", timeout=90)
            check(j2["state"] == "done" and j2["result"]["files"] == 1 and j2["result"]["areas"] == 1, "glob picks the files: one .md area")

            calc = {"calc.py": "def add(a, b):\n    return a - b\n",
                    "tests/test_calc.py": "import sys, unittest\nsys.path.insert(0, '.')\nfrom calc import add\n\n"
                                          "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"}
            code, up = call("POST", "/uploads", raw=targz(calc))
            code, j = call("POST", "/jobs", {"kind": "implement", "upload": up["upload"], "spec": "add must add",
                                             "test_command": "python3 -m unittest discover -s tests -q"})
            code, j = call("GET", f"/jobs/{j['id']}?wait=60", timeout=90)
            r = j.get("result") or {}
            check(j["state"] == "done" and r.get("passed"), f"the implementation agent's tests pass: {j['state']} {j.get('error')} {r.get('attempts')}")
            patch = r.get("patch", "")
            check("+    return a + b" in patch and "-    return a - b" in patch and "test_calc" not in patch,
                  "the patch fixes calc.py and leaves the protected test alone")
            check(r.get("summary") == "add fixed", "finish before a passing run was refused; the later finish ends it")
            with tempfile.TemporaryDirectory() as g:   # the patch applies to the folder it came from
                for n, t in calc.items():
                    os.makedirs(os.path.dirname(os.path.join(g, n)) or g, exist_ok=True); open(os.path.join(g, n), "w").write(t)
                pf = os.path.join(g, "p.patch"); open(pf, "w").write(patch)
                ap = subprocess.run(["patch", "-p1", "-d", g, "-i", pf], capture_output=True, text=True)
                check(ap.returncode == 0 and "a + b" in open(os.path.join(g, "calc.py")).read(), "patch -p1 applies it")

            sbx = f"test ! -e /home && test ! -e /opt && ! (exec 3<>/dev/tcp/127.0.0.1/{port}) 2>/dev/null && touch made.txt"
            code, up = call("POST", "/uploads", raw=targz({"f.txt": "x\n"}))
            code, j = call("POST", "/jobs", {"kind": "implement", "upload": up["upload"], "spec": "SANDBOXCHECK", "test_command": sbx})
            code, j = call("GET", f"/jobs/{j['id']}?wait=60", timeout=90)
            check(j["state"] == "done" and j["result"]["passed"],
                  f"the tests' sandbox has no /home, no /opt and no network: {(j.get('result') or {}).get('test_output', '')[-300:]}")

            code, up = call("POST", "/uploads", who="grok", raw=targz(proj))
            code, g = call("POST", "/jobs", {"kind": "swarm", "upload": up["upload"]}, who="grok")
            check(code == 200 and g["state"] == "awaiting_approval", f"GrokBot's swarm job waits for approval: {g.get('state')}")
            check(until(lambda: g["id"] in open(pings).read(), 5), "the owner's phone gets the request with its id")
            check(call("GET", f"/jobs/{g['id']}")[0] == 404, "Claude cannot see GrokBot's job")
            check(call("POST", f"/jobs/{g['id']}/approve", who="grok")[0] == 403, "GrokBot cannot approve its own job")
            code, a = call("POST", f"/jobs/{g['id']}/approve", who="owner")
            check(code == 200 and a["state"] == "queued", "the owner approves it")
            code, g = call("GET", f"/jobs/{g['id']}?wait=60", who="grok", timeout=90)
            check(g["state"] == "done", f"the approved job runs: {g['state']}")
            code, gc = call("POST", "/jobs", {"kind": "chat", "messages": [{"role": "user", "content": "x"}]}, who="grok")
            code, gc = call("GET", f"/jobs/{gc['id']}?wait=30", who="grok")
            check(gc["state"] == "done", "GrokBot's chat needs no approval")
            code, h = call("POST", "/jobs", {"kind": "implement", "upload": up["upload"], "spec": "s", "test_command": "true"}, who="grok")
            code, h = call("POST", f"/jobs/{h['id']}/cancel", who="grok")
            check(h["state"] == "cancelled", "a held job can be cancelled")
            check(call("POST", "/jobs", {"kind": "swarm", "upload": up["upload"]})[0] == 403, "an upload is its owner's only")

            check(until(lambda: status()["loaded"] == "main", 20), "back on main")
            subprocess.run(["bash", "-c", f"kill $(cut -d' ' -f1 {stubd}/running)"])
            check(until(lambda: status()["held"], 10), "a setup that stops on its own (the thermal guard): held")
            check(until(lambda: "stopped without the service" in open(pings).read(), 5), "and the owner is told")
            code, r = call("POST", "/v1/chat/completions", {"model": "main", "messages": [{"role": "user", "content": "hi"}]})
            check(code == 503, f"while held, chat gets 503: {code}")
            check(call("POST", "/admin/resume", {})[0] == 403, "only an admin resumes")
            call("POST", "/admin/resume", {}, who="owner")
            check(until(lambda: status()["loaded"] == "main"), "after resume, main loads again")

            mcp = subprocess.Popen([sys.executable, os.path.join(LC, "lc-mcp")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True, env={**env, "LC_URL": base, "LC_TOKEN": tok["claude"], "HOME": d})

            def rpc(i, method, params=None):
                mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
                mcp.stdin.flush()
                return json.loads(mcp.stdout.readline())

            init = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
            check(init["result"]["serverInfo"]["name"] == "localcompute", "MCP: initialize")
            mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"); mcp.stdin.flush()
            names = [t["name"] for t in rpc(2, "tools/list")["result"]["tools"]]
            check(names == ["ask", "swarm", "implement", "job", "status", "cancel"], f"MCP: the tools {names}")
            f = os.path.join(d, "note.txt"); open(f, "w").write("the note\n")
            r = rpc(3, "tools/call", {"name": "ask", "arguments": {"prompt": "what?", "files": [f], "thinking": False}})
            check("echo main" in r["result"]["content"][0]["text"], "MCP: ask answers through a chat job")
            projd = os.path.join(d, "proj"); os.makedirs(os.path.join(projd, "tests"))
            for n, t in calc.items():
                open(os.path.join(projd, n), "w").write(t)
            r = rpc(4, "tools/call", {"name": "implement", "arguments": {"path": projd, "spec": "add must add",
                                                                         "test_command": "python3 -m unittest discover -s tests -q"}})
            text = r["result"]["content"][0]["text"]
            jid = text.split()[2]
            r = rpc(5, "tools/call", {"name": "job", "arguments": {"id": jid, "wait_seconds": 60}})
            text = r["result"]["content"][0]["text"]
            check("tests PASS" in text and "apply: patch -p1 -d" in text, f"MCP: implement, then job gives the patch and how to apply it: {text[:200]}")
            r = rpc(6, "tools/call", {"name": "job", "arguments": {"id": "000000000000"}})
            check(r["result"].get("isError"), "MCP: a bad job id is an error, not a crash")
            mcp.stdin.close(); mcp.wait(5)
        finally:
            proc.terminate(); proc.wait(10)
            subprocess.run([setup, "stop"])
            if failed:
                print(open(os.path.join(d, "lcd.log")).read()[-3000:])
    print(f"{ok} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
