import json, glob, sys, statistics as st, os
proj = sys.argv[1]
MEM = ("AGENTS.md", "PROGRESS.md", "DECISIONS.md", "CODEMAP.md")
WRITE = ("write", "edit")
rows, roles = [], {}
for f in sorted(glob.glob(os.path.join(proj, ".agent/sessions/iter-*.jsonl"))):
    calls, peak, ttfc, outs, think_c, other_c, mem_c, reads, turns = {}, 0, None, [], 0, 0, {}, 0, 0
    for line in open(f):
        try: e = json.loads(line)
        except ValueError: continue
        if e.get("type") != "message_end": continue
        m = e.get("message") or {}
        r = m.get("role"); roles[r] = roles.get(r, 0) + 1
        if r == "assistant":
            turns += 1
            u = m.get("usage") or {}
            ctx = u.get("totalTokens") or 0
            peak = max(peak, ctx); outs.append(u.get("output") or 0)
            for c in m.get("content") or []:
                t = c.get("type")
                if t == "thinking": think_c += len(c.get("thinking") or "")
                elif t == "text": other_c += len(c.get("text") or "")
                elif t == "toolCall":
                    a = c.get("arguments") or {}
                    other_c += len(json.dumps(a))
                    calls[c.get("id")] = (c.get("name"), str(a.get("path") or a.get("file_path") or ""))
                    if ttfc is None and c.get("name") in WRITE and not any(a.get("path", "").endswith(x) for x in MEM):
                        ttfc = ctx
        elif r in ("toolResult", "tool"):
            name, path = calls.get(m.get("toolCallId"), ("?", ""))
            n = sum(len(c.get("text") or "") for c in m.get("content") or [] if isinstance(c, dict))
            if name == "read":
                reads += 1
                for x in MEM:
                    if path.endswith(x): mem_c[x] = mem_c.get(x, 0) + n
    if turns == 0: continue
    gen = sum(outs)
    rows.append(dict(it=os.path.basename(f)[5:9], peak=peak, ttfc=ttfc, gen=gen, maxout=max(outs), turns=turns,
                     think_share=think_c / max(1, think_c + other_c), mem=mem_c, reads=reads))
def med(k): v = [r[k] for r in rows if r[k] is not None]; return st.median(v) if v else None
print("message roles:", roles)
print(f"iterations: {len(rows)}")
print(f"median peak ctx: {med('peak'):.0f}  median ctx at first code write: {med('ttfc')}  median turns: {med('turns')}")
print(f"median tokens generated/iter (all stay in ctx, preserve_thinking): {med('gen'):.0f}  -> {st.median([r['gen']/r['peak'] for r in rows if r['peak']]):.0%} of peak ctx")
print(f"thinking share of generated chars (median): {med('think_share'):.0%}")
print(f"largest single response: {max(r['maxout'] for r in rows)} tokens; responses >=16000: {sum(1 for r in rows if r['maxout']>=16000)} iters")
for x in MEM:
    v = [r['mem'].get(x, 0) / 4 for r in rows]
    print(f"{x:13s} read into ctx: median ~{st.median(v):.0f} tok/iter, iters reading it: {sum(1 for t in v if t)}/{len(rows)}")
print("last 6 iters:", [(r['it'], r['peak'], r['ttfc'], round(sum(r['mem'].values())/4)) for r in rows[-6:]])
