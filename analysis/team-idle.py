#!/usr/bin/env python3
"""team-idle.py <main checkout> [agent ids] : where a team's GPU time went - per agent, busy (sessions by kind:
build, plan, goal check, prep) against idle (waits, by what was holding the agent: planning, the goal check, or
dependencies/claims/size), over the hours its loop was running; plus every planning run (000 claim to merge).
Reads <main>/.agent/team/events.jsonl and <main>.<id>/.agent/iterations.jsonl. A wait ends at wait_end, or (loop
stopped or restarted) at the agent's next event of any kind, or at the team's stop event.
First use 2026-10-04 (frontpage, Oct 2-4): b busy 44%, idle planning 14%, goal checks 13%, dependencies 28.5%.
"""
import json, sys, re
from datetime import datetime, timezone
M = sys.argv[1].rstrip("/")
IDS = sys.argv[2:] or ["a", "b"]
P = lambda s: datetime.fromisoformat(s)
ev = [json.loads(l) for l in open(f"{M}/.agent/team/events.jsonl") if l.strip()]
now = datetime.now(timezone.utc)
def cat(r):
    if re.search(r"\b000 held|no open tasks", r): return "planning"
    if re.search(r"\b999 held", r): return "goal check"
    return "deps/claims/size"
# waits per agent by reason category. A wait ends at wait_end, or (loop stopped or restarted: no wait_end) at the
# agent's next event of any kind, or at the team's stop event.
wait = {}; open_w = {}
def close(a, t):
    t0, r = open_w.pop(a); c = cat(r); wait.setdefault(a, {}).setdefault(c, 0); wait[a][c] += max(0, (t - t0).total_seconds())
for e in ev:
    a, t = e["agent"], P(e["time"])
    if e["event"] == "stop":
        for x in list(open_w): close(x, t)
        continue
    if e["event"] == "wait_reason" and a in open_w:
        close(a, t); open_w[a] = (t, e["detail"]); continue
    if a in open_w: close(a, t)
    if e["event"] == "wait_start": open_w[a] = (t, e["detail"])
for a in list(open_w): close(a, now)
# sessions per agent by kind
sess = {}
for a in IDS:
    for l in open(f"{M}.{a}/.agent/iterations.jsonl"):
        if not l.strip(): continue
        r = json.loads(l)
        k = "prep" if r.get("kind") == "prep" else "plan" if "/000-" in r["task"] else "goal check" if "/999-" in r["task"] else "build"
        s = sess.setdefault(a, {}).setdefault(k, [0, 0]); s[0] += 1; s[1] += (P(r["end"]) - P(r["start"])).total_seconds()
        sess[a].setdefault("_first", P(r["start"])); sess[a]["_first"] = min(sess[a]["_first"], P(r["start"]))
# planning episodes: 000 claim -> 000 merge/release
eps, cur = [], None
for e in ev:
    if "/000-" not in e.get("task", ""): continue
    if e["event"] == "claim" and cur is None: cur = [P(e["time"]), e["agent"]]
    elif e["event"] in ("merge", "release") and cur is not None:
        eps.append((cur[0], P(e["time"]), cur[1])); cur = None
if cur: eps.append((cur[0], now, cur[1] + " (open)"))
team0 = P(ev[0]["time"])
H = lambda s: f"{s/3600:5.1f} h"
print(f"team events from {team0:%m-%d %H:%M} UTC to now: {H((now-team0).total_seconds())}")
print("\nPLANNING EPISODES (000 claim -> merge):")
tot = 0
for s, e, a in eps:
    d = (e - s).total_seconds(); tot += d; print(f"  {s.astimezone():%m-%d %H:%M} agent {a}: {d/60:6.0f} min")
print(f"  total {H(tot)} over {len(eps)} episodes")
for a in sorted(sess):
    print(f"\nAGENT {a} (first session {sess[a]['_first'].astimezone():%m-%d %H:%M})")
    busy = 0
    for k, (n, s) in sorted((k, v) for k, v in sess[a].items() if k != "_first"):
        busy += s; print(f"  sessions {k:11s} {n:4d}  {H(s)}")
    w = wait.get(a, {}); tw = sum(w.values())
    for c, s in sorted(w.items()): print(f"  waiting  {c:16s} {H(s)}")
    acc = busy + tw
    print(f"  accounted {H(acc)}: busy {100*busy/acc:4.1f}%  idle {100*tw/acc:4.1f}%" + "".join(f"  [{c} {100*s/acc:4.1f}%]" for c, s in sorted(w.items())))
