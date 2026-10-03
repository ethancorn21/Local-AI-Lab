#!/usr/bin/env bash
# test_decisions_cap.sh ARCHIVER : decisions-archive keeps an open task's newest 3 entries and archives the rest
# (finished tasks: all; PITFALL/LESSON and untagged entries: never; a task id without a task file: untouched), never
# loses or duplicates an entry, is idempotent, and gives a reopened task back only its newest entries. Also the old
# sentinel bug: archived entries of task 999 (the goal check) must survive the next run.
set -u
ARCH=${1:-$HOME/bin/decisions-archive}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cd "$T" && mkdir tasks
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
printf 'Status: open\n# 999\n' > tasks/999-goal-check.md
printf 'Status: done\n# 001\n' > tasks/001-core.md
printf 'Status: done\n# 000\n' > tasks/000-plan.md
{ echo "# DECISIONS"; echo
  echo "## 2026-10-02 000 PITFALL: pip is blocked"; echo "use apt"; echo
  for i in 1 2 3 4 5 6; do echo "## 2026-10-02 999 round $i"; echo "round $i notes"; echo; done
  echo "## 2026-10-02 001 DECISION: core layout"; echo "why"; echo
  echo "## 2026-10-02 001 DECISION: store api"; echo "why"; echo
  echo "## a note without a task id"; echo "x"; echo
  echo "## 2026-10-02 777 DECISION: other branch"; echo "y"; echo
} > DECISIONS.md
count() { grep -c "^## " "$1" 2>/dev/null || echo 0; }
total() { echo $(( $(count DECISIONS.md) + $(count DECISIONS-archive.md) - $(grep -c "^## Archived decisions" DECISIONS.md) )); }
n0=$(count DECISIONS.md)
python3 "$ARCH" . ; rc=$?
[ $rc -eq 3 ] && ok "first run changed the files (rc 3)" || bad "first run rc $rc"
[ "$(grep -c '^## 2026-10-02 999' DECISIONS.md)" -eq 3 ] && grep -q "round 6" DECISIONS.md && grep -q "round 4" DECISIONS.md && ! grep -q "round 3" DECISIONS.md \
  && ok "open task 999: newest 3 stay (rounds 4-6)" || bad "open task 999 entries in DECISIONS.md wrong"
[ "$(grep -c '^## 2026-10-02 999' DECISIONS-archive.md)" -eq 3 ] && ok "999's older 3 archived" || bad "999 archive count"
! grep -q "^## 2026-10-02 001" DECISIONS.md && [ "$(grep -c '^## 2026-10-02 001' DECISIONS-archive.md)" -eq 2 ] && ok "finished task 001: all archived" || bad "001 not archived"
grep -q "000 PITFALL" DECISIONS.md && ok "PITFALL stays" || bad "PITFALL moved"
grep -q "a note without a task id" DECISIONS.md && grep -q "777 DECISION" DECISIONS.md && ok "untagged and fileless-task entries stay" || bad "untagged/777 moved"
[ "$(total)" -eq "$n0" ] && ok "no entry lost or duplicated ($n0)" || bad "entries $(total) != $n0"
r4=$(grep -n "^## .*round 4" DECISIONS.md | cut -d: -f1); r6=$(grep -n "^## .*round 6" DECISIONS.md | cut -d: -f1)
[ "$r4" -lt "$r6" ] && ok "chronological order kept" || bad "order changed"
python3 "$ARCH" . ; rc=$?
[ $rc -eq 0 ] && ok "second run: nothing to change (rc 0)" || bad "second run rc $rc"
sed -i.bak '1s/.*/Status: done/' tasks/999-goal-check.md && python3 "$ARCH" . > /dev/null
! grep -q "^## 2026-10-02 999" DECISIONS.md && [ "$(grep -c '^## 2026-10-02 999' DECISIONS-archive.md)" -eq 6 ] && ok "999 done: all 6 archived" || bad "999 done not archived"
sed -i.bak '1s/.*/Status: in-progress/' tasks/999-goal-check.md && python3 "$ARCH" . > /dev/null
[ "$(grep -c '^## 2026-10-02 999' DECISIONS.md)" -eq 3 ] && grep -q "round 6" DECISIONS.md && ok "999 reopened: only its newest 3 come back" || bad "reopen gave back $(grep -c '^## 2026-10-02 999' DECISIONS.md)"
[ "$(total)" -eq "$n0" ] && ok "still no entry lost or duplicated" || bad "entries $(total) != $n0 after reopen"
exit $fail
