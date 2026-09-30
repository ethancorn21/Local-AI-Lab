#!/usr/bin/env bash
# run.sh : thinking-effort A/B on type1-triage task 004, arms interleaved so drift over the evening hits both.
# X = xhigh (production), M = medium. The live loop must be paused (clean timing: one agent on the GPU).
# Each arm stops when 004 is verified done (no later tasks, no goal check) or after 4 sessions.
set -u
cd "$HOME/ab-effort" || exit 1
bash setup.sh X1 M1 X2 M2 X3 M3
export MAX_ITERS=4 GOAL_CHECKS=0
for arm in X1 M1 X2 M2 X3 M3; do
  case $arm in X*) th=xhigh ;; M*) th=medium ;; esac
  echo "== arm $arm (THINKING=$th) start $(date '+%F %T')"
  THINKING=$th "$HOME/bin/agent-loop" "$HOME/ab-effort/$arm/type1-triage" > "$HOME/ab-effort/$arm/loop.out" 2>&1
  echo "== arm $arm end $(date '+%F %T')"
  python3 grade.py "$HOME/ab-effort/$arm/type1-triage" > "$HOME/ab-effort/$arm/grade.json" 2>&1
done
python3 analyze.py X1 M1 X2 M2 X3 M3 > results.md
echo "== A/B finished $(date '+%F %T')"; touch DONE
