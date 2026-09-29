"""Out-of-distribution Linux host-attack windows from splunk/attack_data (Apache-2.0): auditd and auth.log captures
of 30 ATT&CK techniques, recorded on other machines with other audit rules than AIT.

Labels are weak: a capture is filed under one technique, but not every window in it shows that technique (setup
steps, background noise). Every window gets the technique and its tactic as `weak_tactic`; `malicious` is left to be
confirmed (by the type-2 model and a human spot check) before these windows count as ground truth.
Two renderings per window: as recorded, and with audit rule keys (`key=...`, often named after the technique) removed.
Usage: build_splunk.py OUT.jsonl
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from type1canon import canon, parsers  # noqa: E402

ROOT = "/opt/llm/type1/data/splunk_auditd"
TACTICS = json.load(open("/opt/llm/openjev-lab/data/attack_tactics.json"))   # technique id -> [tactics]
RENAME = {"Defense Evasion": "Stealth"}   # the lab's list follows current ATT&CK


def tactic_of(tid):
    ts = TACTICS.get(tid) or TACTICS.get(tid.split(".")[0]) or []
    ts = [RENAME.get(t, t) for t in ts]
    return ts[0] if ts else "Unknown"


def main(out):
    n = 0
    with open(out, "w") as f:
        for path in sorted(glob.glob(f"{ROOT}/**/*.log", recursive=True)):
            lines = open(path, errors="replace").read().splitlines()
            if not lines:
                continue
            if lines[0].startswith("type="):
                evs = [e for _, e in parsers.parse_audit(lines)]
            elif re.match(r"^\w{3} +\d", lines[0]) and "kern.log" not in path:
                evs = [e for _, e in parsers.parse_syslog(lines, "auth", 2025)]
            else:
                continue       # sysmon XML, kernel logs: not formats the homelab type-1 model reads
            evs.sort(key=lambda e: e["ts"])
            rel = path[len(ROOT) + 1:]
            tid = rel.split("/")[0]
            for k, w in enumerate(canon.windows(evs)):
                nokey = [dict(e, msg=re.sub(r' key=\S+', "", e["msg"])) for e in w]
                f.write(json.dumps({"id": f"splunk/{rel}/{k}", "technique": tid, "weak_tactic": tactic_of(tid),
                                    "source": w[0]["source"], "n": len(w), "text": canon.render_window(w),
                                    "text_nokey": canon.render_window(nokey)}) + "\n")
                n += 1
    print(n, "windows")


if __name__ == "__main__":
    main(sys.argv[1])
