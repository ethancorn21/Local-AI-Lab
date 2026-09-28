"""orient.py PROJECT FIRST LAST : what fills the context before the first code edit, and how sessions end."""
import json, os, re, sys, statistics as st
proj, first, last = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
MEM = re.compile(r"(PROGRESS|DECISIONS|CODEMAP|AGENTS)\.md|codemap/")
TEST = re.compile(r"\b(npm (run )?test|node --test|pytest|playwright)\b")
agg = {k: [] for k in ("memory", "source", "tests", "other_bash", "model_output", "ctx_at_first_edit", "first_prompt")}
for n in range(first, last + 1):
    f = os.path.join(proj, f".agent/sessions/iter-{n:04d}.jsonl")
    if not os.path.exists(f) or os.path.getsize(f) == 0: continue
    calls, c, ctx, done, first_ctx = {}, dict(memory=0, source=0, tests=0, other_bash=0, model_output=0), 0, False, None
    for line in open(f):
        try: e = json.loads(line)
        except ValueError: continue
        if e.get("type") not in ("message", "message_end") or done: continue
        m = e.get("message") or {}
        if m.get("role") == "assistant":
            u = m.get("usage") or {}
            if first_ctx is None: first_ctx = u.get("input") or u.get("totalTokens")
            ctx = u.get("totalTokens") or ctx
            c["model_output"] += u.get("output") or 0
            for x in m.get("content") or []:
                if x.get("type") != "toolCall": continue
                a = x.get("arguments") or {}
                calls[x.get("id")] = (x.get("name"), str(a.get("path") or ""), str(a.get("command") or ""))
                if x.get("name") in ("edit", "write") and not MEM.search(str(a.get("path") or "")):
                    done = True
        elif m.get("role") in ("toolResult", "tool"):
            name, path, cmd = calls.get(m.get("toolCallId"), ("?", "", ""))
            chars = sum(len(x.get("text") or "") for x in m.get("content") or [] if isinstance(x, dict))
            if name == "read": c["memory" if MEM.search(path) else "source"] += chars
            elif name == "bash":
                if MEM.search(cmd) and not TEST.search(cmd): c["memory"] += chars
                elif TEST.search(cmd): c["tests"] += chars
                else: c["other_bash"] += chars
    if not done: continue
    for k in ("memory", "source", "tests", "other_bash"): agg[k].append(c[k] // 4)   # ~4 chars per token
    agg["model_output"].append(c["model_output"]); agg["ctx_at_first_edit"].append(ctx); agg["first_prompt"].append(first_ctx or 0)
print("median tokens before the first code edit, by source (sessions with a code edit: %d)" % len(agg["memory"]))
for k, v in agg.items():
    if v: print(f"  {k:18s} median {int(st.median(v)):6d}   p25 {int(sorted(v)[len(v)//4]):6d}   p75 {int(sorted(v)[3*len(v)//4]):6d}")
