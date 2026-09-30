"""Hidden grader for task 004 (streaming windower): never shown to the agents.

1. equivalence: 300 random streams, arrival order shuffled within 9 s of each event's ts, fed in arrival order on a
   virtual clock, then flushed; the emitted windows must equal canon.windows on the ts-sorted events exactly.
2. flush: nothing before 60 s of silence, the open window at 60 s; a fresh windower emits nothing.
3. late drop: an event 50 s behind the high-water mark is dropped and counted and changes no window.
Usage: grade.py PROJECT_DIR   (prints JSON)
"""
import json
import random
import sys
import traceback

sys.path.insert(0, sys.argv[1])


def ev(ts, i):
    return {"ts": ts, "host": "h", "source": "auth", "msg": f"e{i}"}


def ids(ws):
    return [[e["msg"] for e in w] for w in ws]


def main():
    out = {"import": False, "equivalence": None, "flush": None, "drop": None}
    try:
        from triage.windower import Windower
        from type1canon.canon import windows
        out["import"] = True
    except Exception as e:
        out["error"] = repr(e)
        print(json.dumps(out))
        return
    # 1. equivalence
    ok = 0
    fails = []
    for seed in range(300):
        rng = random.Random(seed)
        t, evs = 1000.0, []
        for i in range(rng.randint(1, 80)):
            t += rng.choice([rng.uniform(0, 2), rng.uniform(0, 20), rng.uniform(0, 120)])
            evs.append(ev(round(t, 3), i))
        arrivals = sorted(evs, key=lambda e: e["ts"] + rng.uniform(0, 9))
        now = [0.0]
        try:
            w = Windower(max_lateness=10.0, clock=lambda: now[0])
            got = []
            for e in arrivals:
                now[0] = max(now[0], e["ts"])
                got += w.feed(e)
            now[0] += 61
            got += w.tick(now[0])
            want = list(windows(sorted(evs, key=lambda e: e["ts"])))
            if ids(got) == ids(want):
                ok += 1
            elif len(fails) < 3:
                fails.append(seed)
        except Exception:
            if len(fails) < 3:
                fails.append(f"{seed}: {traceback.format_exc(limit=1).splitlines()[-1]}")
    out["equivalence"] = f"{ok}/300"
    out["equivalence_fail_seeds"] = fails
    # 2. flush
    try:
        now = [0.0]
        w = Windower(max_lateness=10.0, clock=lambda: now[0])
        fresh = w.tick(1e6)
        for i, ts in enumerate((0.0, 2.0, 5.0)):
            now[0] = ts
            w.feed(ev(ts, i))
        early = w.tick(5.0 + 59.9)
        at = w.tick(5.0 + 60.5)     # the spec leaves exactly 60 s open (">= 60" and "> 60" both fit it)
        out["flush"] = fresh == [] and early == [] and ids(at) == [["e0", "e1", "e2"]]
    except Exception as e:
        out["flush"] = f"error: {e!r}"
    # 3. late drop
    try:
        now = [0.0]
        w = Windower(max_lateness=10.0, clock=lambda: now[0])
        got = []
        for i, ts in enumerate((100.0, 200.0)):
            now[0] = ts
            got += w.feed(ev(ts, i))
        now[0] = 201.0
        got += w.feed(ev(150.0, 2))
        now[0] = 300.0
        got += w.tick(300.0)
        out["drop"] = w.dropped == 1 and ids(got) == [["e0"], ["e1"]]
    except Exception as e:
        out["drop"] = f"error: {e!r}"
    print(json.dumps(out))


if __name__ == "__main__":
    main()
