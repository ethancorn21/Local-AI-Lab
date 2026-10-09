"""turns.py <since> <until> : per-session time split for the frontpage team's sessions (run on the VM as the agent user).

For each session in the window: model time (estimated from the turn's tokens and the card's measured speeds in SPEED),
the time each turn then spent waiting on its tools, by kind (tests, installs/builds, other shell, file read/edit, web),
and gaps. A turn's span runs from its request start to its last tool result (Pi's message timestamps). Prints one
JSON line per session, then the slowest commands. day.py uses only the shares per kind; the model time itself comes
from the servers' counters (on 2026-10-08 the token estimate came within 7% of them).
"""
import json, re, sys
from collections import defaultdict
from datetime import datetime

SINCE, UNTIL = datetime.fromisoformat(sys.argv[1]), datetime.fromisoformat(sys.argv[2])
# per-card speeds from the servers' cumulative counters (tokens per second while decoding / prefilling uncached input)
SPEED = {"a": (93.3, 813.0), "c": (85.0, 734.0), "b": (20.45, 577.0)}
TEST = re.compile(r"(pytest|npm (run )?test|node --test|playwright|test_e2e|\be2e/|\.spec\.mjs|unittest)")
BUILD = re.compile(r"(pip |npm (ci|install)|uv |venv|make\b|python3? -m (pip|venv))")
slow = defaultdict(lambda: [0, 0.0])
for ch in ("a", "b", "c"):
    root = f"/home/agent/projects/frontpage.{ch}/.agent"
    dec, pre = SPEED[ch]
    for l in open(f"{root}/iterations.jsonl"):
        if not l.strip():
            continue
        r = json.loads(l)
        s, e = datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])
        if e <= SINCE or s >= UNTIL:
            continue
        msgs = []
        try:
            for line in open(f"{root}/sessions/iter-{r['iter']:04d}.jsonl"):
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "message_end" and (ev.get("message") or {}).get("timestamp"):
                    msgs.append(ev["message"])
        except FileNotFoundError:
            pass
        o = dict(ch=ch, iter=r["iter"], kind=r.get("kind") or "build", start=r["start"], end=r["end"],
                 wall=(e - s).total_seconds(), model=0.0, tool=defaultdict(float), gap=0.0, turns=0, out=0)
        for i, m in enumerate(msgs):
            if m.get("role") != "assistant":
                continue
            o["turns"] += 1
            u = m.get("usage") or {}
            o["out"] += u.get("output") or 0
            mt = (u.get("input") or 0) / pre + (u.get("output") or 0) / dec
            t0 = m["timestamp"] / 1000
            # results of this turn's tools, then the next assistant message
            j, last_res, nxt = i + 1, None, None
            while j < len(msgs):
                if msgs[j].get("role") == "toolResult":
                    last_res = msgs[j]["timestamp"] / 1000
                elif msgs[j].get("role") == "assistant":
                    nxt = msgs[j]["timestamp"] / 1000
                    break
                j += 1
            end_of_turn = last_res or nxt
            if end_of_turn is None:   # last turn: model time only, rest is session close
                o["model"] += mt
                continue
            span = max(0.0, end_of_turn - t0)
            mt = min(mt, span)
            o["model"] += mt
            calls = [c for c in (m.get("content") or []) if c.get("type") == "toolCall"]
            tt = span - mt
            if calls:
                kinds = []
                for c in calls:
                    n = c.get("name")
                    a = c.get("arguments") or {}
                    if n == "bash":
                        cmd = str(a.get("command") or "")
                        k = "tests" if TEST.search(cmd) else "installs/builds" if BUILD.search(cmd) else "other shell"
                        if tt > 60:
                            key = re.sub(r"\s+", " ", cmd)[:110]
                            slow[(k, key)][0] += 1
                            slow[(k, key)][1] += tt
                    elif n in ("web_search", "web_fetch"):
                        k = "web"
                    elif n == "ask_human":
                        k = "request"
                    else:
                        k = "file read/edit"
                    kinds.append(k)
                order = ["tests", "installs/builds", "web", "other shell", "request", "file read/edit"]
                k = min(kinds, key=order.index)
                o["tool"][k] += tt
            else:
                o["gap"] += tt
            if last_res is not None and nxt is not None:
                o["gap"] += max(0.0, nxt - last_res)
        print(json.dumps(o))
top = sorted(slow.items(), key=lambda kv: -kv[1][1])[:40]
print(json.dumps({"slow": [[k[0], k[1], v[0], round(v[1])] for k, v in top]}))
