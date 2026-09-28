"""memstats.py PROJECT FIRST LAST : how agents actually use the memory files, per session.

For each iteration session: tokens of context when the first code edit happens (orientation cost), which memory
files were read and how much of them (chars returned, whether the read reached the end), archive/codemap/git-log use,
what was written to memory files and how (tool, chars), and how many turns went to reading vs doing.
"""
import json, os, re, sys, glob, statistics as st

proj, first, last = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
MEM = re.compile(r"(^|/)(PROGRESS|DECISIONS|CODEMAP|AGENTS)\.md$|(^|/)codemap/.+\.md$|DECISIONS-archive\.md$")


def kind(path):
    b = os.path.basename(path)
    if "/codemap/" in "/" + path or path.startswith("codemap/"):
        return "codemap/*"
    return b


rows = []
for n in range(first, last + 1):
    f = os.path.join(proj, f".agent/sessions/iter-{n:04d}.jsonl")
    if not os.path.exists(f) or os.path.getsize(f) == 0:
        continue
    calls, reads, writes, bash_mem, seq = {}, {}, {}, [], []
    ctx, ttfc, first_test, turns, peak = 0, None, None, 0, 0
    for line in open(f):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") not in ("message", "message_end"):
            continue
        m = e.get("message") or {}
        role = m.get("role")
        if role == "assistant":
            turns += 1
            u = m.get("usage") or {}
            ctx = u.get("totalTokens") or ctx
            peak = max(peak, ctx)
            for c in m.get("content") or []:
                if c.get("type") != "toolCall":
                    continue
                name, a = c.get("name"), c.get("arguments") or {}
                path = str(a.get("path") or "")
                calls[c.get("id")] = (name, path, a)
                if name in ("edit", "write"):
                    if MEM.search(path):
                        k = kind(path)
                        w = writes.setdefault(k, {"n": 0, "chars": 0, "tools": set()})
                        w["n"] += 1; w["tools"].add(name)
                        w["chars"] += len(a.get("content") or "") + sum(len(x.get("newText") or "") for x in a.get("edits") or []) + len(a.get("newText") or "")
                    elif ttfc is None:
                        ttfc = ctx
                    seq.append("W" if not MEM.search(path) else "m")
                elif name == "read":
                    seq.append("r" if MEM.search(path) else "R")
                elif name == "bash":
                    cmd = a.get("command") or ""
                    if re.search(r"(npm|node --test|pytest|node .*test)", cmd) and first_test is None:
                        first_test = ctx
                    if re.search(r">>?\s*(DECISIONS|PROGRESS|CODEMAP)\.md|(DECISIONS|PROGRESS|CODEMAP)\.md\s*<<", cmd) or re.search(r"cat\s*>>?\s*\S*(DECISIONS|PROGRESS|CODEMAP)", cmd):
                        mm = re.search(r"(DECISIONS|PROGRESS|CODEMAP)\.md", cmd)
                        k = mm.group(0)
                        w = writes.setdefault(k, {"n": 0, "chars": 0, "tools": set()})
                        w["n"] += 1; w["tools"].add("bash>>"); w["chars"] += len(cmd)
                        seq.append("m")
                    elif "DECISIONS-archive" in cmd:
                        bash_mem.append("archive-grep"); seq.append("g")
                    elif re.search(r"git (--no-pager )?log", cmd):
                        bash_mem.append("git-log"); seq.append("l")
                    elif re.search(r"grep|sed -n|head|tail|cat", cmd) and re.search(r"(PROGRESS|DECISIONS|CODEMAP)\.md|codemap/", cmd):
                        bash_mem.append("bash-read-mem"); seq.append("b")
                    else:
                        seq.append("x")
        elif role in ("toolResult", "tool"):
            name, path, a = calls.get(m.get("toolCallId"), ("?", "", {}))
            if name == "read" and MEM.search(path):
                text = "".join(c.get("text") or "" for c in m.get("content") or [] if isinstance(c, dict))
                r = reads.setdefault(kind(path), {"n": 0, "chars": 0, "truncated": 0, "offset_reads": 0})
                r["n"] += 1; r["chars"] += len(text)
                if re.search(r"Use offset=\d+ to continue", text):
                    r["truncated"] += 1
                if a.get("offset"):
                    r["offset_reads"] += 1
    if turns:
        rows.append(dict(it=n, turns=turns, peak=peak, ttfc=ttfc, first_test=first_test, reads=reads, writes=writes,
                         bash_mem=bash_mem, seq="".join(seq)))

print(f"sessions {len(rows)} (iterations {first}-{last})")
def med(v):
    v = [x for x in v if x is not None]
    return int(st.median(v)) if v else None
print("median peak ctx", med([r["peak"] for r in rows]), "| median ctx at first code edit", med([r["ttfc"] for r in rows]),
      "| sessions with no code edit", sum(r["ttfc"] is None for r in rows))
print("\nREADS of memory files: % sessions reading, median chars when read, sessions with a truncated read that never continued")
for k in ("AGENTS.md", "PROGRESS.md", "DECISIONS.md", "CODEMAP.md", "codemap/*", "DECISIONS-archive.md"):
    rs = [r["reads"][k] for r in rows if k in r["reads"]]
    trunc_nocont = sum(1 for x in rs if x["truncated"] and not x["offset_reads"])
    print(f"  {k:22s} {100 * len(rs) // max(1, len(rows)):3d}%  median chars {med([x['chars'] for x in rs])}  reads/session {med([x['n'] for x in rs])}  truncated-never-continued {trunc_nocont}")
for k in ("archive-grep", "git-log", "bash-read-mem"):
    print(f"  {k:22s} {100 * sum(1 for r in rows if k in r['bash_mem']) // max(1, len(rows)):3d}% of sessions")
print("\nWRITES to memory files: % sessions writing, median chars per session, tools used")
for k in ("PROGRESS.md", "DECISIONS.md", "CODEMAP.md", "codemap/*"):
    ws = [r["writes"][k] for r in rows if k in r["writes"]]
    tools = {}
    for x in ws:
        for t in x["tools"]:
            tools[t] = tools.get(t, 0) + 1
    print(f"  {k:22s} {100 * len(ws) // max(1, len(rows)):3d}%  median chars {med([x['chars'] for x in ws])}  tools {tools}")
print("\nACTION SEQUENCE (r=read memory, R=read source, b=bash-read memory, l=git log, g=archive grep, W=code edit, m=memory write, x=other bash)")
for r in rows[-12:]:
    print(f"  {r['it']}: {r['seq'][:110]}")
