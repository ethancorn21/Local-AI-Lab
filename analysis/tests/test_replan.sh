#!/usr/bin/env bash
# test_replan.sh [AGENT_LOOP] : a re-plan after a GOAL.md change starts its session count from 0 (split nudge and
# STALLED flag), and reopening a finished plan drops the GOAL.md change notes that plan already covered, while an
# unfinished plan keeps them (its last change may not be planned in yet).
set -u
LOOP=${1:-$HOME/bin/agent-loop}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cd "$T" && mkdir -p tasks .agent
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
eval "$(sed -n '/^task_runs()/,/^}/p' "$LOOP")"
sed -n '/^  python3 - tasks\/000-plan.md/,/^PY$/p' "$LOOP" | sed '1d;$d' > reopen.py

LEDGER=.agent/iterations.jsonl
e() { echo "{\"iter\":$1,\"task\":\"tasks/$2.md\",\"status_after\":\"$3\",\"verify\":$4}"; }
{ e 1 000-plan done '"rejected"'; e 2 000-plan done '"accepted"'
  for i in 3 4 5; do e $i 000-plan in-progress null; done; e 6 000-plan done '"accepted"'
  e 7 001-core in-progress null; e 8 000-plan in-progress null; e 9 000-plan in-progress null; } > $LEDGER
[ "$(task_runs tasks/000-plan.md)" = 2 ] && ok "re-plan counts from its reopen (2, not 9)" || bad "000 runs $(task_runs tasks/000-plan.md)"
[ "$(task_runs tasks/001-core.md)" = 1 ] && ok "other task counted on its own" || bad "001 runs $(task_runs tasks/001-core.md)"
[ "$(task_runs tasks/002-none.md)" = 0 ] && ok "task never run: 0" || bad "002 runs $(task_runs tasks/002-none.md)"
e 10 000-plan done '"rejected"' >> $LEDGER
[ "$(task_runs tasks/000-plan.md)" = 3 ] && ok "a rejected done claim does not reset the count" || bad "after rejection $(task_runs tasks/000-plan.md)"
: > $LEDGER; [ "$(task_runs tasks/000-plan.md)" = 0 ] && ok "empty ledger: 0" || bad "empty ledger"

plan() {  # plan <status> : a planning task that went through two GOAL.md changes, with a hand-over
  printf 'Status: %s\n# 000: Plan\n\n## Acceptance criteria\n- [x] PLAN.md\n\n## Notes\n- format\n\n' "$1"
  printf '## GOAL.md changed (2026-10-01 10:00)\nchange one\n```diff\n+first\n## not a heading inside the diff\n```\n\n'
  printf '## GOAL.md changed (2026-10-02 10:00)\nchange two\n```diff\n+second\n```\n\n## Hand-over\nnext: write PLAN.md\n'
}
printf '+third\n' > d.diff
plan done > tasks/000-plan.md; python3 reopen.py tasks/000-plan.md d.diff "2026-10-03 10:00"
f=tasks/000-plan.md
[ "$(grep -c '^## GOAL.md changed' $f)" = 1 ] && grep -q '+third' $f && ! grep -q '+first\|+second' $f \
  && ok "finished plan: old change notes dropped, the new one added" || bad "finished plan: $(grep '^## GOAL' $f | tr '\n' ' ')"
grep -q 'Earlier GOAL.md changes are planned in' $f && ok "pointer to git log added under Notes" || bad "no pointer"
[ "$(grep -n '^## ' $f | tail -1 | cut -d: -f2-)" = "## Hand-over" ] && grep -q 'next: write PLAN.md' $f \
  && ok "hand-over kept and still last" || bad "hand-over moved or lost"
head -1 $f | grep -q 'in-progress' && grep -q '^- \[ \] PLAN.md' $f && ok "reopened: in-progress, boxes unticked" || bad "status/boxes"
printf '+fourth\n' > d.diff; sed -i.bak '1s/.*/Status: done/' $f && python3 reopen.py $f d.diff "2026-10-04 10:00"
[ "$(grep -c 'Earlier GOAL.md changes' $f)" = 1 ] && [ "$(grep -c '^## GOAL.md changed' $f)" = 1 ] && grep -q '+fourth' $f \
  && ok "second reopen: one pointer, one change note" || bad "second reopen"

plan in-progress > $f; printf '+third\n' > d.diff; python3 reopen.py $f d.diff "2026-10-03 10:00"
[ "$(grep -c '^## GOAL.md changed' $f)" = 3 ] && grep -q '+first' $f && grep -q '+second' $f && grep -q '+third' $f \
  && ok "unfinished plan: every change note kept" || bad "unfinished plan lost a note"
! grep -q 'Earlier GOAL.md changes' $f && ok "unfinished plan: no pointer" || bad "pointer on unfinished plan"
exit $fail
