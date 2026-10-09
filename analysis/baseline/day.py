"""day.py <data dir> [since] [until] : where each model card's day goes, as shares of a window.

Model time comes from the servers' own counters: the lab archive's per-minute samples of generated and prompt tokens
(hw/servers/*.jsonl on the AI box, concatenated into <data>/servers.jsonl) times seconds per token from each server's
lifetime /metrics totals (LIFE below; re-read them before reusing: vLLM request_decode/prefill_time_seconds_sum,
llama.cpp tokens_predicted_seconds_total / prompt_seconds_total). Sessions, waits and driver test runs come from copies of
the team's ledgers, event log and loop logs in <data>/fp/{main,a,b,c}/.agent/. In-session idle time is split by the
tool each turn waited on, from turns.py output in <data>/turns.jsonl (run turns.py on the VM for the same window).
First run 2026-10-08 (frontpage, Oct 7 21:43 to Oct 8 19:44): see docs/experiments.md#baseline-where-a-cards-day-goes.
"""
import json, re, sys, collections
from datetime import datetime, timedelta, timezone

D = sys.argv[1]
CDT = timezone(timedelta(hours=-5))
W0 = datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime(2026, 10, 7, 21, 43, tzinfo=CDT)
W1 = datetime.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else datetime(2026, 10, 8, 19, 44, 30, tzinfo=CDT)
WS = (W1 - W0).total_seconds()
P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(CDT)
clip = lambda s, e: max(0.0, (min(e, W1) - max(s, W0)).total_seconds())
CARD = {"a": 8080, "c": 8081, "b": 8082}
# lifetime counters read 2026-10-08 19:43: seconds per generated token, seconds per prompt token (vLLM: all prompt tokens incl.
# cache hits, matching the sampler's prompt_tokens; llama.cpp: uncached prompt tokens, matching its sampler field)
LIFE = {8080: (38495.08 / 3591466, 9850.76 / 178416640), 8081: (56944.31 / 4837974, 13970.74 / 219270562),
        8082: (61091.3 / 1249330, 3126.27 / 1804670)}

srv = collections.defaultdict(list)
for l in open(f"{D}/servers.jsonl"):
    r = json.loads(l)
    srv[r["port"]].append((P(r["t"]), r))
def counter_at(port, t, k):
    xs = srv[port]
    best = min(xs, key=lambda x: abs((x[0] - t).total_seconds()))
    return best[1][k], best[0]

ev = [json.loads(l) for l in open(f"{D}/fp/main/.agent/team/events.jsonl") if l.strip()]
turns = collections.defaultdict(list)
for l in open(f"{D}/turns.jsonl"):
    r = json.loads(l)
    if "slow" in r:
        slow = r["slow"]
        continue
    turns[r["ch"]].append(r)

out = {"window": [W0.isoformat(), W1.isoformat()], "hours": WS / 3600, "cards": {}}
for a, port in CARD.items():
    g0, t0 = counter_at(port, W0, "gen_tokens"); g1, t1 = counter_at(port, W1, "gen_tokens")
    p0, _ = counter_at(port, W0, "prompt_tokens"); p1, _ = counter_at(port, W1, "prompt_tokens")
    span = (t1 - t0).total_seconds()
    gen_tok = g1 - g0
    decode = gen_tok * LIFE[port][0] * WS / span
    prefill = (p1 - p0) * LIFE[port][1] * WS / span
    gen_day = gen_tok * 86400 / span
    # sessions
    led = [json.loads(l) for l in open(f"{D}/fp/{a}/.agent/iterations.jsonl") if l.strip()]
    sess = 0.0
    kinds = collections.Counter()
    for r in led:
        s, e = P(r["start"]), P(r["end"])
        c = clip(s, e)
        sess += c
        kinds[r.get("kind") or "build"] += c
    # in-session non-model time by tool kind (per-session shares, scaled by the clipped share of each session)
    tool = collections.Counter()
    est_model = 0.0
    for r in turns[a]:
        s, e = P(r["start"]), P(r["end"])
        f = clip(s, e) / max(1.0, (e - s).total_seconds())
        est_model += r["model"] * f
        for k, v in r["tool"].items():
            tool[k] += v * f
        tool["harness gaps"] += r["gap"] * f
    # waits
    waits = 0.0; open_t = None
    for e in sorted((x for x in ev if x["agent"] == a or x["event"] == "stop"), key=lambda x: P(x["time"])):
        t = P(e["time"])
        if e["event"] == "wait_reason":
            continue
        if open_t is not None:
            waits += clip(open_t, t); open_t = None
        if e["event"] == "wait_start":
            open_t = t
    if open_t is not None:
        waits += clip(open_t, W1)
    # driver oracle: session end -> verified done -> merged into main (loop.log)
    ends = {r["iter"]: P(r["end"]) for r in led}
    oracle = 0.0; cur = None; ver = None; base = 0.0; it_t = None
    for line in open(f"{D}/fp/{a}/.agent/loop.log", errors="replace"):
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*)", line)
        if not m:
            continue
        t = datetime.fromisoformat(m.group(1)).replace(tzinfo=CDT); msg = m.group(2)
        mi = re.match(r"iteration (\d+): ", msg)
        if mi:
            cur = int(mi.group(1)); ver = None; it_t = t; continue
        if msg.startswith("baseline for") and it_t is not None:   # full suite before a task's first session
            base += clip(it_t, t); it_t = None
        if msg.startswith("verified done") and cur in ends:
            oracle += clip(ends[cur], t); ver = t
        elif "merged into main" in msg and ver is not None:
            oracle += clip(ver, t); ver = None
    model = decode + prefill
    insess_idle = max(0.0, sess - model)
    tsum = sum(tool.values()) or 1.0
    split = {k: insess_idle * v / tsum for k, v in tool.items()}
    between = WS - sess
    other_driver = max(0.0, between - waits - oracle - base)
    c = dict(decode=decode, prefill=prefill, **{"in-session " + k: v for k, v in split.items()},
             driver_tests=oracle + base, waiting=waits, driver_other=other_driver)
    out["cards"][a] = dict(port=port, parts=c, gen_tok_day=gen_day, sess=sess, est_model=est_model,
                           kinds=dict(kinds), counter_span_h=span / 3600)
    tot = sum(c.values())
    print(f"\n== {a} port {port}  window {WS/3600:.2f} h  sum {tot/3600:.2f} h  gen/day {gen_day/1e6:.2f}M  "
          f"model(server) {model/3600:.2f} h vs est {est_model/3600:.2f} h  sessions {sess/3600:.2f} h")
    for k, v in sorted(c.items(), key=lambda kv: -kv[1]):
        print(f"   {k:28s} {v/3600:6.2f} h  {100*v/WS:5.1f}%")
out["slow"] = slow
json.dump(out, open(f"{D}/day.json", "w"), indent=1)
