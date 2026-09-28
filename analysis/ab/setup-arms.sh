#!/usr/bin/env bash
# setup-arms.sh NAME:VARIANT ... : fresh copies of hollowdeep at the A/B start commit (right after the 025 split), one
# per arm. VARIANT A = AGENTS-A.md (notes in PROGRESS.md), B = AGENTS-B.md (notes in the task file's ## Hand-over).
# Tasks after 025 are removed; the live task-ownership registry is copied minus tasks that are not in the copy, so a
# task the agent creates in the copy is registered as its own.
set -euo pipefail
START=876e157a74a370bc55f36bae1f0e88e46c68048e
LIVE=$HOME/projects/hollowdeep
for spec in "$@"; do
  arm=${spec%%:*}; var=${spec#*:}; D=$HOME/ab/$arm/hollowdeep
  rm -rf "$HOME/ab/$arm"; mkdir -p "$HOME/ab/$arm"
  git clone -q "$LIVE" "$D"; cd "$D"
  git reset -q --hard "$START"; git remote remove origin; git checkout -q -b "ab-$arm"
  git rm -q tasks/02[6-9]-*.md tasks/03[0-9]-*.md
  cp "$HOME/ab/AGENTS-$var.md" AGENTS.md
  if [ "$var" = B ]; then
    python3 - <<'PY'
import re
notes = open("PROGRESS.md").read().strip()
f = "tasks/025-gadgets-meds.md"; t = open(f).read()
t = re.sub(r"(?ms)^## Hand-over\n.*\Z", "", t).rstrip("\n") + "\n\n## Hand-over\n\n" + notes + "\n"
open(f, "w").write(t)
PY
  fi
  git add -A; git commit -q -m "[ab] arm $arm (variant $var): test copy from $START"
  mkdir -p .agent
  python3 - "$LIVE/.agent/tasks.json" .agent/tasks.json <<'PY'
import glob, json, os, re, sys
reg = json.load(open(sys.argv[1]))
present = {re.match(r"(\d{3}[a-z]*)-", os.path.basename(f)).group(1) for f in glob.glob("tasks/[0-9]*.md")}
json.dump({k: v for k, v in reg.items() if k in present}, open(sys.argv[2], "w"), indent=1, sort_keys=True)
PY
  echo "arm $arm ($var): $(git log --oneline -1 | cut -c1-60) | registry $(jq length .agent/tasks.json) tasks"
done
