"""What failing test runs led to, in the frontpage team's sessions since argv[1].
For each failing test run inside a session: were the failing tests ones this session had not touched (pre-existing) or
ones it wrote/edited earlier in the session (new)? Then: until the next clean run (or the session's end), did the agent
change app code (frontpage/), test code (tests/, e2e/), or both? Also collects pytest --durations lines."""
import json, os, re, sys, collections
from datetime import datetime

SINCE = datetime.fromisoformat(sys.argv[1]).timestamp()
RUN = re.compile(r"(pytest|npm (run )?test|playwright test|node --test|\.spec\.mjs)")
FAILED = re.compile(r"(?:^|\n)\s*FAILED (tests/[^\s:]+|e2e/[^\s:]+)(?:::(\S+))?")
SUMMARY = re.compile(r"(\d+) failed")
PASSED = re.compile(r"(\d+) passed")
DUR = re.compile(r"^\s*([\d.]+)s (call|setup|teardown)\s+(tests/\S+)", re.M)
WRITE_BASH = re.compile(r"(?:cat\s*>{1,2}\s*|tee\s+(?:-a\s+)?|sed -i[^ ]* .*? )(\S+)")

def kind(path):
    p = path.split("/frontpage.")[-1] if "/frontpage." in path else path
    p = re.sub(r"^[abc]/", "", p)
    p = re.sub(r"^/home/agent/projects/frontpage/", "", p)
    if re.search(r"(^|/)(tests|e2e)/", p) or re.search(r"(test_|\.spec\.mjs|e2e_seed)", p):
        return "test", p
    if re.search(r"(^|/)frontpage/", p):
        return "app", p
    return "other", p

episodes = []
dur = {}
runs = collections.Counter()
for ch in "abc":
    d = f"/home/agent/projects/frontpage.{ch}/.agent/sessions"
    for fn in sorted(os.listdir(d)):
        p = os.path.join(d, fn)
        if os.path.getmtime(p) < SINCE:
            continue
        msgs = []
        for line in open(p, errors="replace"):
            if '"message_end"' not in line:
                continue
            try:
                m = json.loads(line)["message"]
            except (ValueError, KeyError):
                continue
            if (m.get("timestamp") or 0) / 1000 >= SINCE:
                msgs.append(m)
        touched_tests = set()
        calls = {}
        open_ep = None
        for m in msgs:
            if m.get("role") == "assistant":
                for c in m.get("content") or []:
                    if c.get("type") != "toolCall":
                        continue
                    a = c.get("arguments") or {}
                    n = c.get("name")
                    paths = []
                    if n in ("edit", "write"):
                        paths = [str(a.get("path") or "")]
                    elif n == "bash":
                        cmd = str(a.get("command") or "")
                        calls[c.get("id")] = cmd
                        paths = [x for x in WRITE_BASH.findall(cmd) if not x.startswith("/tmp") and not x.startswith("-")]
                    for path in paths:
                        k, rel = kind(path)
                        if k == "test":
                            touched_tests.add(rel.split("::")[0])
                        if open_ep is not None and k in ("app", "test"):
                            open_ep[k] += 1
                            if open_ep["first"] is None:
                                open_ep["first"] = k
            elif m.get("role") == "toolResult":
                cmd = calls.get(m.get("toolCallId"), "")
                if not RUN.search(cmd) or re.match(r"\s*(cd [^&]+&&\s*)?(tail|cat|grep|sleep)", cmd):
                    continue
                t = " ".join(c.get("text", "") for c in m.get("content") or [] if isinstance(c, dict))
                for s, ph, tid in DUR.findall(t):
                    dur[tid] = max(dur.get(tid, 0), float(s))
                nf = sum(int(x) for x in SUMMARY.findall(t[-3000:]))
                fails = FAILED.findall(t)
                np_ = sum(int(x) for x in PASSED.findall(t[-3000:]))
                runs["runs"] += 1
                if nf == 0 and not fails:
                    if np_ > 0:
                        runs["clean"] += 1
                        if open_ep is not None:
                            open_ep["resolved"] = True
                            episodes.append(open_ep); open_ep = None
                    continue
                runs["failing"] += 1
                files = sorted({f for f, _ in fails})
                pre = [f for f in files if f not in touched_tests]
                new = [f for f in files if f in touched_tests]
                if open_ep is None:
                    open_ep = dict(ch=ch, sess=fn, app=0, test=0, first=None, resolved=False,
                                   pre=pre, new=new, nfail=nf or len(fails))
        if open_ep is not None:
            episodes.append(open_ep)

def outcome(e):
    if not e["resolved"]:
        return "unresolved in session"
    if e["app"] and not e["test"]:
        return "app code only"
    if e["test"] and not e["app"]:
        return "test code only"
    if e["app"] and e["test"]:
        return "both"
    return "neither (rerun passed)"

tab = collections.Counter()
for e in episodes:
    who = "pre-existing tests" if e["pre"] and not e["new"] else "new/edited tests" if e["new"] and not e["pre"] else "mixed" if e["pre"] else "unnamed"
    tab[(who, outcome(e))] += 1
print(json.dumps({"runs": runs, "episodes": len(episodes),
                  "table": sorted([[k[0], k[1], v] for k, v in tab.items()], key=lambda r: (r[0], -r[2])),
                  "slowest": sorted(dur.items(), key=lambda kv: -kv[1])[:30],
                  "dur_total_known": round(sum(dur.values())), "dur_n": len(dur)}, indent=0))
