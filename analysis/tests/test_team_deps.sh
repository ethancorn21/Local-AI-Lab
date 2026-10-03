#!/usr/bin/env bash
# test_team_deps.sh [DRIVER_DIR] : a subtask's dependency on its own parent is void (frontpage 2026-10-03: 221a
# "Depends on: 221" while 221 was split and waited for its subtasks - both agents idle), and only another agent's
# claim makes an idle agent wait: with nothing but its own blocked claims it goes on to the deadlock path.
set -u
D=${1:-$HOME/bin}
T=$(mktemp -d); trap 'kill $bpid 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
P=$T/proj; mkdir -p "$P/tasks" "$P/.agent/team/claims" "$P/.agent/team/loops"; cd "$P"
t() { printf 'Status: %s\n# %s\n\nDepends on: %s\nTouches: src/x.py\n' "$2" "$1" "$3" > "tasks/$1.md"; }
t 221-parent split "219, 220"; t 221a-first open 221; t 221b-second open "221a"; t 221c-third open "221, 221b"
t 219-done done none; t 220-done done none; t 230-other open "221"
sleep 600 & bpid=$!; echo $$ > .agent/team/loops/a.pid; echo $bpid > .agent/team/loops/b.pid
as() { ( export TEAM_DIR=$P AGENT_ID=$1; eval "$(sed -n '/^status_of()/p; /^task_id()/p' "$D/agent-loop")"; log() { :; }
         . "$D/agent-team-lib"; eval "$2" ); }
[ -z "$(as a 'unmet_deps tasks/221a-first.md')" ] && ok "221a's dependency on its parent 221 is void" || bad "221a: $(as a 'unmet_deps tasks/221a-first.md')"
[ "$(as a 'unmet_deps tasks/221b-second.md')" = 221a ] && ok "a sibling dependency still counts (221b waits for 221a)" || bad "221b: $(as a 'unmet_deps tasks/221b-second.md')"
[ "$(as a 'unmet_deps tasks/221c-third.md' | tr '\n' ' ')" = "221b " ] && ok "mixed: parent dropped, sibling kept" || bad "221c: $(as a 'unmet_deps tasks/221c-third.md' | tr '\n' ' ')"
[ "$(as a 'unmet_deps tasks/230-other.md')" = 221 ] && ok "another top-level task still waits for 221" || bad "230: $(as a 'unmet_deps tasks/230-other.md')"
[ -z "$(as a 'unmet_deps tasks/221-parent.md')" ] && ok "the parent's own dependencies (done) are met" || bad "221: $(as a 'unmet_deps tasks/221-parent.md')"
mkdir .agent/team/claims/221 && echo "a $$ $(date +%s) $P/tasks/221-parent.md" > .agent/team/claims/221/owner
! as a 'others_hold_claims' && ok "only a's own claim: a does not wait for itself" || bad "a waits on its own claim"
as b 'others_hold_claims' && ok "for b, a's claim means wait" || bad "b does not see a's claim"
mkdir .agent/team/claims/230 && echo "b $bpid $(date +%s) $P/tasks/230-other.md" > .agent/team/claims/230/owner
as a 'others_hold_claims' && ok "b holds a task: a waits" || bad "a ignores b's claim"
exit $fail
