#!/usr/bin/env bash
# test_plan_schedule.sh [DRIVER_DIR] : the team plan check for shared files (plan-schedule + team_plan_check).
# A file many tasks share is fine when their dependencies already put them in a line (frontpage 2026-10-04: the old
# per-file count rejected such a plan 16 sessions in a row); independent tasks that all touch one file get advice
# naming it; tasks that wait on each other always send the plan back. The advice sends a plan back once per GOAL.md
# version: the second claim is accepted (logged), a GOAL.md change gives the advice again; a missing Touches: line
# sends it back every time.
set -u
D=$(cd "${1:-$HOME/bin}" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
PS="python3 $D/plan-schedule"
tab=$'\t'

# the replay alone
out=$(printf "101${tab}${tab}102 103 104 105${tab}feed.html\n102${tab}101${tab}101 103 104 105${tab}feed.html\n103${tab}102${tab}101 102 104 105${tab}feed.html\n104${tab}103${tab}101 102 103 105${tab}feed.html\n105${tab}104${tab}101 102 103 104${tab}feed.html\n" | $PS 2); rc=$?
[ $rc -eq 0 ] && [ -z "$out" ] && ok "five tasks on one file, already in a dependency line: no advice" || bad "chain rc $rc: $out"
in6=$(for i in 1 2 3 4 5 6; do o=$(for j in 1 2 3 4 5 6; do [ $j != $i ] && printf ' 20%s' $j; done); printf "20%s${tab}${tab}%s${tab}config.py src/m%s.py\n" $i "$o" $i; done)
out=$(printf '%s\n' "$in6" | $PS 2); rc=$?
[ $rc -eq 3 ] && grep -q "6 rounds, 3 without" <<<"$out" && grep -q "config.py (" <<<"$out" && ! grep -q "src/m" <<<"$out" \
  && ok "six independent tasks sharing config.py: advice naming config.py (6 rounds vs 3)" || bad "independent rc $rc: $out"
out=$(printf "301${tab}302${tab}${tab}a.py\n302${tab}301${tab}${tab}b.py\n" | $PS 2); rc=$?
[ $rc -eq 4 ] && grep -q "301 (waits on 302)" <<<"$out" && ok "a dependency cycle: tasks named, rc 4" || bad "cycle rc $rc: $out"
out=$(printf "221a${tab}221${tab}${tab}x.py\n221b${tab}221a${tab}${tab}y.py\n" | $PS 2); rc=$?
[ $rc -eq 0 ] && ok "a subtask's dependency on its own parent is void" || bad "parent dep rc $rc: $out"
out=$(printf "401${tab}${tab}402${tab}a.py\n402${tab}${tab}401${tab}a.py\n403${tab}${tab}${tab}b.py\n" | $PS 2); rc=$?
[ $rc -eq 0 ] && ok "one pair sharing a file: within the tolerance, no advice" || bad "small rc $rc: $out"

# team_plan_check in a checkout: the driver's own helpers, the advice once per GOAL.md version
mkdir -p "$T/p/tasks" "$T/p/.agent/team/agents/a" "$T/p/.agent/team/agents/b" && cd "$T/p"
export PATH="$D:$PATH" TEAM_DIR=$T/p LOG=$T/p/loop.log
eval "$(sed -n '/^log() {/p; /^status_of() {/p; /^task_id() {/p' "$D/agent-loop")"
. "$D/agent-team-lib"
echo "goal v1" > GOAL.md
for i in 1 2 3 4 5 6; do printf 'Status: open\n# 20%s\n\nDepends on: none\n\nTouches: config.py, src/m%s.py\n' $i $i > tasks/20$i-m$i.md; done
o1=$(team_plan_check); o2=$(team_plan_check)
grep -q "shared files make tasks wait" <<<"$o1" && ok "first claim: sent back with the advice" || bad "first claim: $o1"
[ -z "$o2" ] && grep -q "plan accepted with the schedule advice already given once" loop.log \
  && ok "second claim: accepted, logged" || bad "second claim: '$o2' / $(cat loop.log 2>/dev/null)"
echo "goal v2" > GOAL.md; o3=$(team_plan_check)
grep -q "shared files make tasks wait" <<<"$o3" && ok "GOAL.md changed: the advice once more" || bad "after GOAL change: $o3"
printf 'Status: open\n# 207\n\nDepends on: none\n' > tasks/207-x.md
o4=$(team_plan_check); o5=$(team_plan_check)
grep -q "tasks/207-x.md has no \`Touches:\` line" <<<"$o4" && grep -q "has no \`Touches:\` line" <<<"$o5" \
  && ok "missing Touches: sends the plan back every time" || bad "structural: $o4 / $o5"
rm tasks/207-x.md tasks/20[2-6]-*.md
for i in 2 3 4 5 6; do printf 'Status: open\n# 20%s\n\nDepends on: 20%s\n\nTouches: config.py, src/m%s.py\n' $i $((i - 1)) $i > tasks/20$i-m$i.md; done
echo "goal v3" > GOAL.md; o6=$(team_plan_check)
[ -z "$o6" ] && ok "the same files in a dependency line: no advice" || bad "chain in checkout: $o6"
exit $fail
