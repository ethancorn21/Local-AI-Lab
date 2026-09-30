"""Hidden grader v2 for task 004 (streaming windower): categories aimed at where implementations differ.

Only behaviour the task pins down is scored. The reference is canon.windows on the kept events sorted by ts, ties in
arrival order (a stable sort of the arrival sequence, as the file input does).

Task 004 contradicts itself on flushing: rule (c) says flush 60 s after the stream's newest event, while exact
equality with canon.windows under max_lateness needs the flush to wait until no late event can still join the open
window (up to max_lateness longer). Both readings are accepted: windowing categories flush far past the end of every
stream, `flush_timing` accepts a flush anywhere from 60 s to 60 s + max_lateness after the newest event, and the
contradicting case (a late event after a flush at 60 s) is reported under `spec_conflict`, not scored.

Every case runs under an alarm (a BaseException, so graded code cannot swallow it); a hang fails that case.
Timestamps are real epoch magnitudes (~1.79e9) unless a category says otherwise.
Usage: grade2.py PROJECT_DIR   (prints one JSON object: per-category "passed/total", first failures, notes)
"""
import json
import random
import signal
import sys
import time

sys.path.insert(0, sys.argv[1])
BASE = 1_790_000_000.0
LATE = 10.0
END = 1000.0          # flush this far past the end of a stream: every reading of the flush rule has fired by then


class Hang(BaseException):
    pass


def _alarm(*_):
    raise Hang()


signal.signal(signal.SIGALRM, _alarm)


def ev(ts, i, **extra):
    return {"ts": ts, "host": "h", "source": "auth", "msg": f"e{i}", **extra}


def ids(ws):
    return [[e["msg"] for e in w] for w in ws]


def reference(arrivals, windows, dropped=()):
    kept = [e for e in arrivals if e["msg"] not in dropped]
    return ids(windows(sorted(kept, key=lambda e: e["ts"])))   # sorted() is stable: ties keep arrival order


def kept_and_dropped(arrivals, late):
    """What the spec keeps: an event more than `late` behind the high-water mark of kept events is dropped."""
    hwm, dropped = None, set()
    for e in arrivals:
        if hwm is not None and hwm - e["ts"] > late:
            dropped.add(e["msg"])
            continue
        hwm = e["ts"] if hwm is None else max(hwm, e["ts"])
    return dropped


def stream(W, arrivals, late=LATE, tick_every=None):
    """Feed arrivals in order on a virtual clock (the arrival's ts, never going back); optionally tick periodically
    like the production loop; then flush far past the end."""
    now = [arrivals[0]["ts"] if arrivals else BASE]
    w = W(max_lateness=late, clock=lambda: now[0])
    got = []
    for e in arrivals:
        if tick_every:
            while now[0] + tick_every < e["ts"]:
                now[0] += tick_every
                got += w.tick(now[0])
        now[0] = max(now[0], e["ts"])
        got += w.feed(e)
    now[0] += END
    got += w.tick(now[0])
    return w, ids(got)


def jittered(evs, rng, jitter=9.0):
    return sorted(evs, key=lambda e: e["ts"] + rng.uniform(0, jitter))


def run_case(fn, limit):
    signal.alarm(limit)
    try:
        return bool(fn())
    except Hang:
        return f"hang ({limit} s)"
    except Exception as e:  # a crash is a failure of that case
        return f"error: {type(e).__name__}: {e}"[:120]
    finally:
        signal.alarm(0)


def main():
    out = {"notes": {}}
    try:
        from triage.windower import Windower as W
        from type1canon.canon import windows
    except Exception as e:
        print(json.dumps({"import": False, "error": repr(e)}))
        return
    cats = {}

    def cat(name, cases, limit=10):
        res = [run_case(c, limit) for c in cases]
        ok = sum(r is True for r in res)
        cats[name] = f"{ok}/{len(res)}"
        bad = [f"#{i}: {r}" for i, r in enumerate(res) if r is not True][:3]
        if bad:
            out.setdefault("first_failures", {})[name] = bad

    def equal(arr, late=LATE, tick_every=None, check_drops=False):
        dropped = kept_and_dropped(arr, late)
        w, got = stream(W, arr, late, tick_every)
        return got == reference(arr, windows, dropped) and (not check_drops or w.dropped == len(dropped))

    # 1. random streams, gaps 0-120 s, arrivals within 9 s of their ts
    def rand_case(seed):
        def f():
            rng = random.Random(seed)
            t, evs = BASE + rng.random(), []
            for i in range(rng.randint(1, 120)):
                t += rng.choice([rng.uniform(0, 2), rng.uniform(0, 20), rng.uniform(0, 120)])
                evs.append(ev(t, i))
            return equal(jittered(evs, rng))
        return f
    cat("random_equivalence", [rand_case(s) for s in range(300)])

    # 2. span boundary: an event exactly max_span after the window's first event joins it; just after, it does not
    def span_case(offset, jitter, seed):
        def f():
            rng = random.Random(seed)
            t0 = BASE + 0.25
            evs = [ev(t0, 0), ev(t0 + 30, 1), ev(t0 + offset, 2), ev(t0 + offset + 5, 3)]
            return equal(jittered(evs, rng) if jitter else evs)
        return f
    cat("span_boundary", [span_case(o, j, s) for o in (59.999, 60.0, 60.001, 60.5) for j in (False, True)
                          for s in range(3)])

    # 3. count boundary: bursts around 16 events inside one span, in order and jittered
    def count_case(n, jitter, seed):
        def f():
            rng = random.Random(seed)
            evs = [ev(BASE + k * 0.3, k) for k in range(n)] + [ev(BASE + 200 + k, 100 + k) for k in range(3)]
            return equal(jittered(evs, rng, 2.0) if jitter else evs)
        return f
    cat("count_boundary", [count_case(n, j, s) for n in (15, 16, 17, 31, 32, 33, 48, 49) for j in (False, True)
                           for s in range(2)])

    # 4. lateness boundary: exactly max_lateness behind the high-water mark is kept (and sorted in); more is dropped
    def late_case(behind):
        def f():
            arr = [ev(BASE, 0), ev(BASE + 20, 1), ev(BASE + 20 - behind, 2), ev(BASE + 25, 3)]
            return equal(arr, check_drops=True)
        return f
    cat("lateness_boundary", [late_case(b) for b in (0.0, 9.0, 9.999, 10.0, 10.001, 15.0)])

    # 5. adversarial: ~40% of events held back and released just before (or just after) they become too late
    def adv_case(seed):
        def f():
            rng = random.Random(seed)
            t, evs = BASE, []
            for i in range(rng.randint(10, 150)):
                t += rng.choice([rng.uniform(0, 1), rng.uniform(0, 8), rng.uniform(40, 70)])
                evs.append(ev(t, i))
            arr, held = [], []
            for e in evs:
                (held if rng.random() < 0.4 else arr).append(e)
                hwm = max(x["ts"] for x in arr) if arr else e["ts"]
                for h in [h for h in held if hwm - h["ts"] > LATE - 0.5]:
                    held.remove(h)
                    arr.append(h)
            return equal(arr + held, check_drops=True)
        return f
    cat("adversarial_lateness", [adv_case(s) for s in range(200)])

    # 6. ties: many equal timestamps, including across the 16-event boundary (arrival order breaks ties).
    # The +3 s key shift reorders arrivals across nearby timestamps too, not only among ties; still within lateness.
    def tie_case(seed):
        def f():
            rng = random.Random(seed)
            evs, t = [], BASE
            for i in range(rng.randint(5, 60)):
                t += rng.choice([0, 0, 0, 1, 30, 70])
                evs.append(ev(t, i))
            return equal(sorted(evs, key=lambda e: e["ts"] + rng.choice([0, 0, 3])))
        return f
    cat("ties", [tie_case(s) for s in range(100)])

    # 7. periodic ticks like the production loop (every 5 s), in streams whose quiet periods are unambiguous
    # (gaps inside a burst <= 25 s, between bursts >= 85 s: every reading of the flush rule gives canon's split)
    def tick_case(seed):
        def f():
            rng = random.Random(seed)
            t, evs = BASE, []
            for i in range(rng.randint(5, 100)):
                t += rng.choice([rng.uniform(0, 5), rng.uniform(0, 25), rng.uniform(85, 200)])
                evs.append(ev(t, i))
            return equal(jittered(evs, rng), tick_every=5.0)
        return f
    cat("periodic_ticks", [tick_case(s) for s in range(150)])

    # 8. other max_lateness values: 0 (no reordering allowed: anything behind the high-water mark is dropped), 3, 30,
    # and 90 (longer than the 60 s span: windows must be held until no late event can still join them)
    def lateness_param_case(late, seed):
        def f():
            rng = random.Random(seed)
            t, evs = BASE, []
            for i in range(rng.randint(5, 120)):
                t += rng.choice([rng.uniform(0, 3), rng.uniform(0, 30), rng.uniform(0, 150)])
                evs.append(ev(t, i))
            return equal(jittered(evs, rng, late * 1.2 if late else 2.0), late=late, check_drops=True)
        return f
    cat("lateness_param", [lateness_param_case(l, s) for l in (0.0, 3.0, 30.0, 90.0) for s in range(40)])

    # 9. small timestamps: streams starting at ts 0.0 (catches `if not high_water:` treating 0.0 as "no events")
    def zero_case(seed):
        def f():
            rng = random.Random(seed)
            t, evs = 0.0, []
            for i in range(rng.randint(2, 60)):
                evs.append(ev(t, i))
                t += rng.choice([0.0, rng.uniform(0, 5), rng.uniform(0, 90)])
            return equal(jittered(evs, rng, 5.0), check_drops=True)
        return f
    cat("zero_timestamps", [zero_case(s) for s in range(60)])

    # 10. flush timing: nothing before 60 s after the newest event; everything by 60 s + max_lateness; several windows
    # pending at once come out in order; nothing twice; a fresh stream after a flush starts clean; tick() without an
    # argument reads the injected clock
    def flush_case(n_events, late):
        def f():
            now = [BASE]
            w = W(max_lateness=late, clock=lambda: now[0])
            evs = [ev(BASE + k * 0.2, k) for k in range(n_events)]
            got = []
            for e in evs:
                now[0] = e["ts"]
                got += w.feed(e)
            last = now[0]
            early = w.tick(last + 59.9)
            got += early + w.tick(last + 60 + late + 0.5)
            again = w.tick(last + 5000)
            return early == [] and again == [] and ids(got) == reference(evs, windows)
        return f

    def flush_resume():
        now = [BASE]
        w = W(max_lateness=LATE, clock=lambda: now[0])
        got = []
        for i, ts in enumerate((BASE, BASE + 3)):
            now[0] = ts
            got += w.feed(ev(ts, i))
        now[0] = BASE + 3 + 60 + LATE + 1
        got += w.tick()
        for i, ts in enumerate((BASE + 300, BASE + 301), start=2):
            now[0] = ts
            got += w.feed(ev(ts, i))
        now[0] = BASE + 2000
        got += w.tick()
        return ids(got) == [["e0", "e1"], ["e2", "e3"]]

    def flush_fresh():
        w = W(max_lateness=LATE, clock=lambda: BASE)
        return w.tick(BASE + 1e6) == [] and w.tick() == []

    cat("flush_timing", [flush_case(n, l) for n in (1, 3, 16, 20, 40) for l in (LATE, 0.0)]
        + [flush_resume, flush_fresh])

    # 11. two instances interleaved do not share state; events come back as the same dicts, extra keys intact
    def two_instances():
        now = [BASE]
        a = W(max_lateness=LATE, clock=lambda: now[0])
        b = W(max_lateness=LATE, clock=lambda: now[0])
        ga, gb = [], []
        for k in range(40):
            now[0] = BASE + k * 3 + 1
            ga += a.feed(ev(BASE + k * 3, k))
            gb += b.feed(ev(BASE + k * 3 + 1, 100 + k))
        now[0] += END
        ga += a.tick(now[0])
        gb += b.tick(now[0])
        return (ga and ids(ga) == ids(windows([ev(BASE + k * 3, k) for k in range(40)]))
                and ids(gb) == ids(windows([ev(BASE + k * 3 + 1, 100 + k) for k in range(40)])))

    def passthrough():
        now = [BASE]
        w = W(max_lateness=LATE, clock=lambda: now[0])
        evs = [ev(BASE + k, k, extra={"k": k}, raw="x" * k) for k in range(5)]
        got = []
        for e in evs:
            now[0] = e["ts"]
            got += w.feed(e)
        got += w.tick(now[0] + END)
        flat = [e for win in got for e in win]
        return len(flat) == 5 and all(a == b for a, b in zip(flat, evs))
    cat("isolation_and_passthrough", [two_instances, passthrough])

    # 12. scale: 200k jittered events over a day, and 30k events inside one second
    def big(n, spread, seed):
        def f():
            rng = random.Random(seed)
            evs = sorted((ev(BASE + rng.uniform(0, spread), i) for i in range(n)), key=lambda e: e["ts"])
            arr = jittered(evs, rng, 5.0)
            t = time.perf_counter()
            got = stream(W, arr)[1]
            out["notes"].setdefault("scale_seconds", {})[f"{n}_in_{spread}s"] = round(time.perf_counter() - t, 2)
            return got == reference(arr, windows)
        return f
    cat("scale", [big(200_000, 86_400, 1), big(30_000, 1.0, 2)], limit=60)

    # Reported, not scored: which flush rule the implementation follows, and the case where the two readings differ
    # (events at 0, 2, 5; tick at 65.5; then an event with ts 58 arrives 8 s late: canon puts it in the first window).
    def probe():
        now = [BASE]
        w = W(max_lateness=LATE, clock=lambda: now[0])
        got = []
        for i, ts in enumerate((0.0, 2.0, 5.0)):
            now[0] = BASE + ts
            got += w.feed(ev(BASE + ts, i))
        now[0] = BASE + 65.5
        got += w.tick(now[0])
        flushed_at_60 = bool(got)
        now[0] = BASE + 66
        got += w.feed(ev(BASE + 58, 3))
        now[0] = BASE + END
        got += w.tick(now[0])
        return flushed_at_60, ids(got) == [["e0", "e1", "e2", "e3"]]
    signal.alarm(10)
    try:
        f60, exact = probe()
        out["notes"]["spec_conflict"] = {"flushes_60s_after_newest": f60, "canon_exact_when_late_event_follows": exact}
    except BaseException as e:  # noqa: BLE001 - report, never score
        out["notes"]["spec_conflict"] = f"error: {type(e).__name__}"
    finally:
        signal.alarm(0)

    out["categories"] = cats
    fr = [int(v.split("/")[0]) / int(v.split("/")[1]) for v in cats.values()]
    out["score"] = round(sum(fr) / len(fr), 4)
    out["perfect_categories"] = f"{sum(f == 1 for f in fr)}/{len(fr)}"
    print(json.dumps(out))


if __name__ == "__main__":
    main()
