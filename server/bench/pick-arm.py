#!/usr/bin/env python3
"""pick-arm: of the arms named, the one that started and decodes fastest on a short prompt (bench-load.py single).
Why (2026-10-18): quality depends on the weights, not on the cards or the drafter, so each model's quality run uses
its fastest arm. Falls back to the last arm named.

usage: pick-arm.py ARM...
"""
import json, statistics, sys

best = None
for arm in sys.argv[1:]:
    try:
        rows = [json.loads(l) for l in open(f"results/{arm}.load.jsonl")]
        started = json.loads(open(f"results/{arm}.switch.json").read().splitlines()[-1]).get("first_token_s")
    except (OSError, IndexError, ValueError):
        continue
    tps = [r["decode_tps"] for r in rows if r.get("test") == "short_cold" and r.get("decode_tps")]
    if tps and started is not None and (best is None or statistics.median(tps) > best[0]):
        best = (statistics.median(tps), arm)
print(best[1] if best else sys.argv[-1])
