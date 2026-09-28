#!/usr/bin/env bash
# Build the two A/B copies of hollowdeep at the commit right after the 025 split (run as the agent user).
# Both: tasks after 025 removed (the run ends when 025 is done), current AGENTS.md for the arm, the live task-ownership
# registry (025a/025b are the agent's), no git remote. Arm B: the PROGRESS.md notes of that commit moved into 025's
# "## Hand-over" (the split session held 025), so both arms start from the same information.
set -euo pipefail
START=876e157a74a370bc55f36bae1f0e88e46c68048e
LIVE=$HOME/projects/hollowdeep
for arm in A B; do
  D=$HOME/ab/$arm/hollowdeep
  rm -rf "$HOME/ab/$arm"; mkdir -p "$HOME/ab/$arm"
  git clone -q "$LIVE" "$D"; cd "$D"
  git reset -q --hard "$START"; git remote remove origin
  git checkout -q -b ab-$arm
  git rm -q tasks/02[6-9]-*.md tasks/03[0-9]-*.md
  cp "$HOME/ab/AGENTS-$arm.md" AGENTS.md
  if [ "$arm" = B ]; then
    python3 - <<'PY'
import re
notes = open("PROGRESS.md").read().strip()
f = "tasks/025-gadgets-meds.md"; t = open(f).read()
t = re.sub(r"(?ms)^## Hand-over\n.*\Z", "", t).rstrip("\n") + "\n\n## Hand-over\n\n" + notes + "\n"
open(f, "w").write(t)
PY
  fi
  git add -A; git commit -q -m "[ab] arm $arm: test copy from $START (tasks after 025 removed, arm AGENTS.md)"
  mkdir -p .agent; cp "$LIVE/.agent/tasks.json" .agent/tasks.json
  echo "arm $arm: $(git log --oneline -1) | tasks: $(ls tasks | tr '\n' ' ')"
done
