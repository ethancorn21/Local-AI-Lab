"""timing.py PROJECT : where wall time and tokens go in vLLM-era sessions (per-turn timestamps + usage)."""
import json, os, re, sys, statistics as st
from datetime import datetime
proj = sys.argv[1]
led = [json.loads(l) for l in open(os.path.join(proj, ".agent/iterations.jsonl")) if l.strip()]
iters = [r["iter"] for r in led if (r.get("harness") or {}).get("vllm")]
TEST = re.compile(r"\b(npm (run )?test|node --test|playwright|game-shot|full-run)\b")
ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
tot = dict(model=0, prefill_est=0, tool_test=0, tool_other=0, sessions=0, turns=0)
out_tok = reas_tok = inp_tok = cached = 0
per_turn_out, per_turn_reas, speeds, test_runs, test_secs, turn_secs, reas_capped = [], [], [], 0, [], [], 0
sess_wall = []
for it in iters:
    f = os.path.join(proj, f".agent/sessions/iter-{it:04d}.jsonl")
    if not os.path.exists(f) or os.path.getsize(f) == 0: continue
    ents = []
    for line in open(f):
        try: e = json.loads(line)
        except ValueError: continue
        if e.get("type") == "message" and e.get("timestamp"): ents.append(e)
    if len(ents) < 3: continue
    tot["sessions"] += 1; sess_wall.append(ts(ents[-1]["timestamp"]) - ts(ents[0]["timestamp"]))
    prev_t, calls = ts(ents[0]["timestamp"]), {}
    last_asst_t = None
    for e in ents:
        m, t = e["message"], ts(e["timestamp"])
        role = m.get("role")
        if role == "assistant":
            dt = t - prev_t; tot["model"] += dt; tot["turns"] += 1; turn_secs.append(dt)
            u = m.get("usage") or {}
            o, r = u.get("output") or 0, u.get("reasoning") or 0
            out_tok += o; reas_tok += r; inp_tok += u.get("input") or 0; cached += u.get("cacheRead") or 0
            per_turn_out.append(o); per_turn_reas.append(r)
            if r >= 15500: reas_capped += 1
            if dt > 5 and o > 200: speeds.append(o / dt)
            for c in m.get("content") or []:
                if c.get("type") == "toolCall":
                    calls[c.get("id")] = (c.get("name"), str((c.get("arguments") or {}).get("command") or ""))
            last_asst_t = t
        elif role == "toolResult" and last_asst_t is not None:
            name, cmd = calls.get(m.get("toolCallId"), ("?", ""))
            d = max(0.0, t - last_asst_t)
            if name == "bash" and TEST.search(cmd):
                tot["tool_test"] += d; test_runs += 1; test_secs.append(d)
            else:
                tot["tool_other"] += d
            last_asst_t = t  # parallel results: count only the increment
        prev_t = t
S = tot["sessions"]; wall = sum(sess_wall)
print(f"vLLM-era sessions: {S} (iters {min(iters)}-{max(iters)}), turns {tot['turns']}, median session {st.median(sess_wall)/60:.1f} min")
print(f"WALL TIME: model {100*tot['model']/wall:.0f}%  |  test runs {100*tot['tool_test']/wall:.0f}%  |  other tools {100*tot['tool_other']/wall:.0f}%")
print(f"test runs: {test_runs} ({test_runs/S:.1f}/session), median {st.median(test_secs):.0f}s, p90 {sorted(test_secs)[int(.9*len(test_secs))]:.0f}s, total {tot['tool_test']/3600:.1f} h")
print(f"TOKENS generated: {out_tok}  reasoning share {100*reas_tok/max(1,out_tok):.0f}%  | turns with reasoning >= 15.5k (budget cap 16k): {reas_capped}")
print(f"per-turn output: median {st.median(per_turn_out):.0f}, p90 {sorted(per_turn_out)[int(.9*len(per_turn_out))]:.0f}; per-turn reasoning median {st.median(per_turn_reas):.0f}, p90 {sorted(per_turn_reas)[int(.9*len(per_turn_reas))]:.0f}")
print(f"effective generation speed per turn (output/turn time, incl. prefill): median {st.median(speeds):.0f} tok/s")
print(f"prompt tokens {inp_tok}, served from prefix cache {100*cached/max(1,inp_tok+cached):.0f}% (cacheRead {cached})")
print(f"turn time: median {st.median(turn_secs):.0f}s, p90 {sorted(turn_secs)[int(.9*len(turn_secs))]:.0f}s")
