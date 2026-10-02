#!/usr/bin/env python3
"""team-watch.py <project> <state file> : new alert/info lines for a team project since the last call (run as root).

ALERT: merge/sync conflicts, failures after a merge, dependency deadlocks, stale claims, duplicate task numbers, failed
merges into main, an agent waiting 20+ min (not counting the wait while 000 is being planned), a wait that ended after
15+ min, a loop that is gone while the team has not finished, a STUCK.md. DONE: the team wrote STOP. info: merges.
"""
import glob
import json
import os
import sys
import time
from datetime import datetime

name, state_path = sys.argv[1], sys.argv[2]
P = f"/home/agent/projects/{name}"
T = f"{P}/.agent/team"
st = json.load(open(state_path)) if os.path.exists(state_path) else {"n": 0, "alerted": []}
ev = [json.loads(l) for l in open(f"{T}/events.jsonl") if l.strip()]
out = []


def once(key, line):
    if key not in st["alerted"]:
        st["alerted"].append(key)
        out.append(line)


ALERT = {"merge_conflict", "sync_conflict", "post_merge_fail", "deps_deadlock", "stale_claim", "dup_task_id", "merge_failed"}
planning_only = lambda d: all(x.strip().startswith("000 ") for x in d.split(";") if x.strip())
# Held up by the other agent = there is work, but it waits for a dependency or overlaps the other agent's files.
# Waiting while the only open tasks are held by others (end of the queue, or planning) is expected, not blocking.
blocked = lambda d: "waits for" in d or "touches the same files" in d
start_of = {}   # agent -> detail of its latest wait_start (a wait that began while 000 was planned is expected)
for e in ev[:st["n"]]:
    if e["event"] == "wait_start":
        start_of[e["agent"]] = e["detail"]
for e in ev[st["n"]:]:
    if e["event"] == "wait_start":
        start_of[e["agent"]] = e["detail"]
    when = e["time"][11:16]
    if e["event"] in ALERT:
        out.append(f"ALERT {when} agent {e['agent']} {e['event']} {e['task']} {e['detail'][:160]}")
    elif e["event"] == "merge":
        out.append(f"info {when} agent {e['agent']} merged {e['task']}")
    elif e["event"] == "wait_end" and int(e["detail"] or 0) >= 900:
        kind = "ALERT" if blocked(start_of.get(e["agent"], "")) else "info"
        out.append(f"{kind} {when} agent {e['agent']} waited {int(e['detail']) // 60} min before taking {e['task']} "
                   f"(waiting since: {start_of.get(e['agent'], '?')[:160]})")
st["n"] = len(ev)

waiting = {}
for e in ev:
    if e["event"] == "wait_start":
        waiting[e["agent"]] = e
    elif e["event"] == "wait_end":
        waiting.pop(e["agent"], None)
for a, e in waiting.items():
    mins = (time.time() - datetime.fromisoformat(e["time"]).timestamp()) / 60
    if mins >= 20 and blocked(e["detail"]):
        once(f"wait:{e['time']}", f"ALERT agent {a} has been held up {mins:.0f} min: {e['detail'][:240]}")
    elif mins >= 20 and not planning_only(e["detail"]):
        once(f"idle:{e['time']}", f"info agent {a} idle {mins:.0f} min, no free work (end of queue): {e['detail'][:160]}")

stop = os.path.exists(f"{T}/STOP")
for f in glob.glob(f"{T}/loops/*.pid"):
    a, pid = os.path.basename(f)[:-4], open(f).read().strip()
    if not stop and not os.path.exists(f"/proc/{pid}"):
        log = f"{P}.{a}/.agent/loop.log"
        tail = open(log).read().splitlines()[-2:] if os.path.exists(log) else []
        once(f"dead:{a}:{pid}", f"ALERT agent {a} loop is not running (pid {pid}); last log: {' | '.join(tail)[:300]}")
    if os.path.exists(f"{P}.{a}/STUCK.md"):
        once(f"stuck:{a}:{os.path.getmtime(f'{P}.{a}/STUCK.md')}", f"ALERT agent {a} wrote STUCK.md")
if stop:
    once("stop", f"DONE {open(f'{T}/STOP').read().strip()}")

json.dump(st, open(state_path, "w"))
print("\n".join(out))
