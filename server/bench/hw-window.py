#!/usr/bin/env python3
"""hw-window: power and heat of each bench arm, from the thermal guard's log (/var/log/hwtemps.csv, one row per GPU
every 15 s) over the arm's own time window, read from a bench-phase.sh log ("=== <time> <arm>: start" ... "done").
Per arm and GPU: mean and peak watts while busy (utilization over 50%), peak temperature, thermal-slowdown samples;
and the CPU's peak. Why (2026-10-17): a serving setup's spec sheet includes what it costs in power and heat.

usage: hw-window.py results/phase-1.log [--csv /var/log/hwtemps.csv]
"""
import argparse, csv, re
from datetime import datetime


def windows(log):
    open_, out = {}, []
    for line in open(log):
        m = re.match(r"=== (\S+) (\S+): (quality start|hard start|start|quality done|hard done|done|FAILED)", line)
        if not m:
            continue
        t, arm, what = datetime.fromisoformat(m.group(1)).replace(tzinfo=None), m.group(2), m.group(3)
        if "start" in what:
            open_[arm] = t
        elif arm in open_:
            out.append((arm, open_.pop(arm), t))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log"); ap.add_argument("--csv", default="/var/log/hwtemps.csv")
    a = ap.parse_args()
    # per window, not per arm: an arm that failed to start and ran later has two windows (10-18)
    wins = [w for w in windows(a.log) if (w[2] - w[1]).total_seconds() >= 60]
    rows = [[] for _ in wins]
    with open(a.csv) as f:
        for r in csv.DictReader(f):
            t = datetime.fromisoformat(r["time"])
            for k, (arm, t0, t1) in enumerate(wins):
                if t0 <= t <= t1:
                    rows[k].append(r)
    for (arm, t0, t1), rs in zip(wins, rows):
        cpu = max((float(r["cpu_max_core_c"]) for r in rs), default=None)
        print(f"{arm}  {t0:%H:%M}-{t1:%H:%M}  CPU peak {cpu} C")
        for g in sorted({r["name"] for r in rs}):
            gr = [r for r in rs if r["name"] == g]
            busy = [float(r["gpu_w"]) for r in gr if float(r["gpu_util_pct"] or 0) > 50]
            print(f"   {g:12s} busy {len(busy) * 15 / 60:5.1f} min  mean {sum(busy) / len(busy) if busy else 0:5.0f} W  "
                  f"peak {max((float(r['gpu_w']) for r in gr), default=0):5.0f} W  peak {max(float(r['gpu_c']) for r in gr):4.0f} C  "
                  f"slowdown samples {sum(r['thermal_slowdown'] not in ('0', '') for r in gr)}")


if __name__ == "__main__":
    main()
