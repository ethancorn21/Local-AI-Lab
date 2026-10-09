"""lockwait.py <since> : how long agents' own foreground test runs waited for the team's test lock (run on the VM as the
agent user). Counts each direct pytest / npm test tool result once; background runs that are polled with tail are
skipped because their output repeats. See harness/driver/pylib/agent_testlock.py for the lock.
"""
import json, re, os, sys
from datetime import datetime
SINCE = datetime.fromisoformat(sys.argv[1]).timestamp()
pat = re.compile(r"(lock taken|gave up) after (\d+) s")
TEST = re.compile(r"(pytest|npm (run )?test)")
for ch in "abc":
    d = f"/home/agent/projects/frontpage.{ch}/.agent/sessions"
    lo = hi = n = gave = direct = 0
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if os.path.getmtime(p) < SINCE:
            continue
        cmds = {}
        for line in open(p, errors="replace"):
            if '"message_end"' not in line:
                continue
            try:
                m = json.loads(line)["message"]
            except (ValueError, KeyError):
                continue
            if (m.get("timestamp") or 0) / 1000 < SINCE:
                continue
            if m.get("role") == "assistant":
                for c in m.get("content") or []:
                    if c.get("type") == "toolCall" and c.get("name") == "bash":
                        cmds[c.get("id")] = str((c.get("arguments") or {}).get("command") or "")
            elif m.get("role") == "toolResult":
                cmd = cmds.get(m.get("toolCallId"), "")
                # direct runs only: the command itself starts pytest/npm test in the foreground
                if not TEST.search(cmd) or "nohup" in cmd or re.search(r"&\s*($|\n|;)", cmd) or re.match(r"\s*(cd [^&]+&&\s*)?(tail|cat|grep|sleep|for |while )", cmd):
                    continue
                direct += 1
                t = " ".join(c.get("text", "") for c in m.get("content") or [] if isinstance(c, dict))
                w = [(k, int(s)) for k, s in pat.findall(t)]
                if w:
                    n += 1; lo += max(s for _, s in w); hi += sum(s for _, s in w); gave += any(k == "gave up" for k, _ in w)
    print(ch, "direct test runs:", direct, "with a lock wait:", n, "gave up:", gave, "wait h low %.2f high %.2f" % (lo / 3600, hi / 3600))
