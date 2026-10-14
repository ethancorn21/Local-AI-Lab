"""between.py <data> <since> <until> : each agent's time outside its sessions, by what the driver was doing.

Reads <data>/fp/{a,b,c}/.agent/{iterations.jsonl,loop.log} (copies of a team's checkouts, as for day.py) and walks the
loop log: from "verified done" to the merge or rejection is the test gate, an "iteration N:" line followed by "baseline
for ... at its first hand-out" is a baseline test run, "nothing to take" / "still waiting" lines are waiting, "waiting
for a job" is waiting for the lead (job mode). Time inside a session is counted as session.
Why: on 2026-10-13 day.py put a 174-minute three-agent wait (frontpage, 10-09 10:30-13:24) under "driver other",
because the team event log had no wait_end for it; the loop log says what the driver was doing at every moment.
"""
import json, re, sys, collections
from datetime import datetime, timedelta, timezone
CDT = timezone(timedelta(hours=-5))
D = sys.argv[1]; W0 = datetime.fromisoformat(sys.argv[2]); W1 = datetime.fromisoformat(sys.argv[3])
P = lambda s: datetime.fromisoformat(s).astimezone(CDT)
def state(msg):
    if msg.startswith("verified done"): return "test gate (verify+merge)"
    if re.match(r"iteration \d+: ", msg): return "pre-session"
    if msg.startswith(("team: nothing to take", "team: nothing to build", "team: still waiting")): return "waiting"
    if msg.startswith("waiting for a job"): return "waiting for the lead"
    if msg.startswith("STUCK") or "stopped" in msg[:40]: return "stopped"
    return "other driver"
out = {}
for a in "abc":
    led = []
    for l in open(f"{D}/fp/{a}/.agent/iterations.jsonl"):
        try: led.append(json.loads(l))
        except ValueError: pass
    sess = [(P(r["start"]), P(r["end"])) for r in led if P(r["end"]) > W0 and P(r["start"]) < W1]
    lines = []
    for line in open(f"{D}/fp/{a}/.agent/loop.log", errors="replace"):
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*)", line)
        if m: lines.append((datetime.fromisoformat(m.group(1)).replace(tzinfo=CDT), m.group(2)))
    # timeline of states, one second resolution is overkill: walk intervals
    tot = collections.Counter(); st = "other driver"; prev = W0
    pts = [(t, m) for t, m in lines if W0 <= t <= W1] + [(W1, "")]
    segs = []
    for i, (t, m) in enumerate(pts):
        segs.append((prev, t, st))
        if m.startswith("baseline for") and "first hand-out" in m and segs and segs[-1][2] == "pre-session":
            p0, p1, _ = segs[-1]; segs[-1] = (p0, p1, "baseline test run")
        st = state(m) if m else st
        prev = t
    def insess(s, e):
        return sum(max(0.0, (min(e, se) - max(s, ss)).total_seconds()) for ss, se in sess)
    for s, e, k in segs:
        span = (e - s).total_seconds(); ins = insess(s, e)
        tot["session"] += ins; tot[k] += span - ins
    out[a] = tot
    W = (W1 - W0).total_seconds()
    print(f"== {a}  " + "  ".join(f"{k} {v/3600:.2f}h ({100*v/W:.1f}%)" for k, v in tot.most_common()))
