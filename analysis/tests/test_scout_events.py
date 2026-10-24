#!/usr/bin/env python3
"""scout.py --events writes one JSON line per step of every scout, in the shape hive-feed (the public swarm page's
feeder) reads: run, start, ask, read, search, report, finish, end. No model: a stub chat server answers with scripted
tool calls. Also: without --events nothing is written.

usage: python3 analysis/tests/test_scout_events.py [path/to/scout.py]
"""
import http.server, json, os, subprocess, sys, tempfile, threading

SCOUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "..", "server", "bench", "scout.py")


class Stub(http.server.BaseHTTPRequestHandler):
    """turn 1: read the first file of the area; turn 2: search, report, finish"""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        turn = 1 + sum(1 for m in body["messages"] if m["role"] == "assistant")
        area = body["messages"][1]["content"].split("YOUR AREA thoroughly:\n")[1].split("\n\n")[0]
        path = area.splitlines()[0].lstrip("- ").split(" lines ")[0]
        if turn == 1:
            calls = [("read_file", {"path": path, "start": 1, "lines": 150})]
        else:
            calls = [("search", {"pattern": "def ", "path": "."}), ("report", {"path": path, "line": 3, "finding": "wrong boundary"}),
                     ("finish", {"summary": "covered"})]
        msg = {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]}
        out = json.dumps({"choices": [{"message": msg}], "usage": {"prompt_tokens": 1000 * turn, "completion_tokens": 40}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out)))
        self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    with tempfile.TemporaryDirectory() as d:
        tgt = os.path.join(d, "target"); os.makedirs(os.path.join(tgt, "pkg"))
        open(os.path.join(tgt, "pkg", "a.py"), "w").write("".join(f"x{i} = {i}\n" for i in range(30)))
        open(os.path.join(tgt, "pkg", "b.py"), "w").write("".join(f"def f{i}():\n    return {i}\n" for i in range(25)))   # 50 lines
        assign, run_out, ev = os.path.join(d, "assign.json"), os.path.join(d, "run.json"), os.path.join(d, "events.jsonl")
        subprocess.run([sys.executable, SCOUT, "split", "--target", tgt, "--agents", "2", "--out", assign], check=True, capture_output=True)
        subprocess.run([sys.executable, SCOUT, "run", "--url", url, "--target", tgt, "--assign", assign, "--agents", "2", "--out", run_out,
                        "--rules", "2", "--cap", "32000", "--events", ev, "--label", "testrun"], check=True, capture_output=True)
        lines = [json.loads(l) for l in open(ev)]
        ok = 0

        def check(cond, what):
            nonlocal ok
            print(("ok   " if cond else "FAIL ") + what); ok += bool(cond)

        run_ev = [e for e in lines if e["ev"] == "run"]
        check(len(run_ev) == 1 and run_ev[0]["run"] == "testrun" and run_ev[0]["target"] == "target", "one run event, labelled")
        check(run_ev[0]["sizes"] == {"pkg/a.py": 30, "pkg/b.py": 50}, f"run.sizes has both files' line counts: {run_ev[0]['sizes']}")
        check(run_ev[0]["agents"] == 2 and run_ev[0]["cap"] == 32000 and run_ev[0]["thinking"] is False, "run carries agents, cap, thinking")
        for sid in ("s000", "s001"):
            evs = [e["ev"] for e in lines if e["s"] == sid]
            check(evs == ["start", "ask", "read", "ask", "search", "report", "finish", "end"], f"{sid} sequence: {evs}")
            rd = next(e for e in lines if e["s"] == sid and e["ev"] == "read")
            check(rd["start"] == 1 and rd["end"] in (30, 40, 50) and rd["path"].startswith("pkg/") and rd["turn"] == 1, f"{sid} read carries path, start, end, turn: {rd}")
            rp = next(e for e in lines if e["s"] == sid and e["ev"] == "report")
            check(rp["line"] == 3 and rp["finding"] == "wrong boundary" and rp["ctx"] == 2000, f"{sid} report carries line, finding, ctx: {rp}")
            en = next(e for e in lines if e["s"] == sid and e["ev"] == "end")
            check(en["end"] == "finish" and en["turns"] == 2 and en["findings"] == 1, f"{sid} end: {en}")
        check(all("t" in e and e["run"] == "testrun" for e in lines), "every line has a time and the run id")
        res = json.load(open(run_out))
        check(len(res["scouts"]) == 2 and all(len(s["findings"]) == 1 for s in res["scouts"]), "the run's own output is unchanged")
        # without --events: nothing written
        ev2 = os.path.join(d, "none.jsonl")
        subprocess.run([sys.executable, SCOUT, "run", "--url", url, "--target", tgt, "--assign", assign, "--agents", "2", "--out", run_out,
                        "--rules", "2", "--cap", "32000"], check=True, capture_output=True)
        check(not os.path.exists(ev2) and len([json.loads(l) for l in open(ev)]) == len(lines), "without --events nothing is written")
        total = 3 + 2 * 4 + 3
        print(f"{ok}/{total} ok"); sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
