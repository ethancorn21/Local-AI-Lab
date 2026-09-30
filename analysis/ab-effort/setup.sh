#!/usr/bin/env bash
# setup.sh ARM... : fresh copies of type1-triage at the start of task 004 (streaming windower), one per arm, for the
# thinking-effort A/B (xhigh vs medium). Later tasks are removed so each arm stops when 004 is verified done, and 004
# gets a fixed interface paragraph (same for every arm) so the hidden grader can call the result.
set -euo pipefail
START=56a50bd
LIVE=$HOME/projects/type1-triage
for arm in "$@"; do
  D=$HOME/ab-effort/$arm/type1-triage
  rm -rf "$HOME/ab-effort/$arm"; mkdir -p "$HOME/ab-effort/$arm"
  git clone -q "$LIVE" "$D"; cd "$D"
  git reset -q --hard "$START"; git remote remove origin; git checkout -q -b "ab-$arm"
  git rm -q tasks/00[5-9]-*.md
  python3 - <<'PY'
f = "tasks/004-windower.md"
t = open(f).read()
iface = """## Interface (fixed: an external check calls it)
`triage.windower.Windower(max_lateness=10.0, clock=None)`, one instance per `(host, source)` stream; `clock` is a
zero-argument callable returning the current time in the events' `ts` units. `feed(event)` returns the list of
windows this event sealed (each window a list of event dicts, in order; usually empty). `tick(now=None)` flushes a
stream that has had no kept event for 60 s and returns the windows that came out. `dropped` (a property) counts
events dropped as too late.

"""
open(f, "w").write(t.replace("## Acceptance criteria", iface + "## Acceptance criteria", 1))
PY
  git add -A; git commit -q -m "[ab-effort] arm $arm: copy at $START, tasks after 004 removed, 004 interface fixed"
  mkdir -p .agent
  python3 - "$LIVE/.agent/tasks.json" .agent/tasks.json <<'PY'
import glob, json, os, re, sys
reg = json.load(open(sys.argv[1]))
present = {re.match(r"(\d{3}[a-z]*)-", os.path.basename(f)).group(1) for f in glob.glob("tasks/[0-9]*.md")}
json.dump({k: v for k, v in reg.items() if k in present}, open(sys.argv[2], "w"), indent=1, sort_keys=True)
PY
  echo "arm $arm: $(git log --oneline -1 | cut -c1-70)"
done
