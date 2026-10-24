#!/bin/bash
# 10-19: skip the 4-bit 27B's rules-1 runs of 100 scouts and the thinking run in chain-next4's swarm phase
# (bench-phase.sh swarm, PID $1). Under rules 1 the 27B reads in batches and hits the 32k cap before reporting (16 and 32
# scouts: all capped, 2 and 5 found), and 100 scouts of 32-64k overrun its 831k-token KV pool, so each run would take
# hours to measure the cap again. chain-next7 runs the 27B under rules 2 instead. The phase's longhard step still runs.
cd /opt/llm/sweep
while kill -0 "$1" 2>/dev/null; do
  for p in $(pgrep -f "scout.py run .*results/w4-27b-tp2b\.scout-(100-|32-32k-think)"); do
    out=$(tr '\0' ' ' < /proc/$p/cmdline | grep -o 'results/[^ ]*\.json')
    kill "$p" && echo "=== $(date -Is) skipped $out: rules 1 for the 27B, chain-next7 runs it under rules 2" >> results/phase-swarm.log
    sleep 2; rm -f "$out" "${out%.json}.log"
  done
  pgrep -f "longctx.py run .*w4-27b-tp2b" >/dev/null && { echo "=== $(date -Is) skip-tp2b: longhard started, done" >> results/phase-swarm.log; exit 0; }
  sleep 3
done
