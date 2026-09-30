"""Summarise the thinking-effort A/B: per arm wall time, sessions, output/thinking tokens, done verified, grade.
Usage: analyze.py ARM...   (run in ~/ab-effort; prints a markdown table)
"""
import glob
import json
import os
import re
import sys
from datetime import datetime

rows = []
for arm in sys.argv[1:]:
    P = f"{os.environ['HOME']}/ab-effort/{arm}/type1-triage"
    log = open(f"{P}/.agent/loop.log").read() if os.path.exists(f"{P}/.agent/loop.log") else ""
    stamps = [datetime.strptime(m, "%Y-%m-%d %H:%M:%S") for m in re.findall(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", log, re.M)]
    wall = (stamps[-1] - stamps[0]).total_seconds() / 60 if len(stamps) > 1 else float("nan")
    sessions = len(re.findall(r"^\S+ \S+ iteration \d+:", log, re.M))
    done = "verified done: tasks/004" in log
    out_tok = think_chars = text_chars = turns = 0
    for f in glob.glob(f"{P}/.agent/sessions/iter-*.jsonl"):
        for line in open(f):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "message_end" or d["message"].get("role") != "assistant":
                continue
            m = d["message"]
            turns += 1
            out_tok += m.get("usage", {}).get("output", 0)
            for c in m.get("content", []):
                if c.get("type") == "thinking":
                    think_chars += len(c.get("thinking", ""))
                elif c.get("type") in ("text", "toolCall"):
                    text_chars += len(c.get("text", "")) + len(json.dumps(c.get("arguments", {})))
    try:
        g = json.loads(open(f"{os.environ['HOME']}/ab-effort/{arm}/grade.json").read().strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        g = {}
    rows.append([arm, f"{wall:.1f}", str(sessions), str(turns), str(out_tok),
                 f"{think_chars / max(think_chars + text_chars, 1):.2f}", "yes" if done else "no",
                 str(g.get("equivalence")), str(g.get("flush")), str(g.get("drop"))])
head = ["arm", "wall min", "sessions", "turns", "output tokens", "thinking share", "004 verified",
        "hidden equivalence", "flush", "drop"]
print("| " + " | ".join(head) + " |")
print("|" + "---|" * len(head))
for r in rows:
    print("| " + " | ".join(r) + " |")
