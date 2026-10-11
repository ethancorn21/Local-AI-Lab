#!/usr/bin/env bash
# test_team_blocked_files.sh [DRIVER_DIR] : a task blocked on the human holds no files (agent-team-lib, busy_tasks).
# ancient-works 2026-10-10: c's 001 (Touches: pipeline/, tools/, tests/) was finished and waiting on a ruling from
# 22:38; every other open task lists a test file, so agent a wrote prep notes for 2.5 h. Checked here:
#   - while c builds 001, a task on its files waits (the overlap rule stands)
#   - once 001 is blocked on the human, that task is free for a, and the first task a takes
#   - c keeps its claim on 001; when the answer sets 001 back in progress, new tasks on its files wait again
# Real git repo and worktrees; agents a and c are this shell's functions run with different AGENT_IDs. Run on the VM.
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/driver" && pwd)}
T=$(mktemp -d); trap 'kill $apid $cpid 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home PATH="$D:$PATH"; mkdir -p "$HOME/.agent-kit/agents"
fns() {  # the definition of one function of agent-loop (one line or a block)
  awk -v n="$1" '!p && $0 ~ "^"n"\\(\\) *\\{" { print; if ($0 ~ /; }[[:space:]]*(#.*)?$/) exit; p = 1; next } p { print } p && /^}/ { exit }' "$D/agent-loop"
}
as() {  # as <agent> <commands> : in that agent's checkout, with its identity and the driver's helpers
  ( cd "$P.$1" && export TEAM_DIR=$P AGENT_ID=$1 HOME TEAM_WAIT_S=0
    eval "$(for f in status_of task_id handover_of pending_subtasks workable next_task commit_leftovers ask_field asks_with inbox_pending dead_status agent_commits; do fns $f; done)"
    log() { echo "LOG $AGENT_ID $*" >> "$T/log"; }
    . "$D/agent-team-lib"; eval "$2" )
}
task() { printf 'Status: %s\n# %s\n\nDepends on: none\nTouches: %s\n\n## Acceptance criteria\n- [ ] works\n' "$2" "$1" "$3" > "tasks/$1.md"; }
claim() { local p; eval "p=\$${1}pid"; mkdir -p "$P/.agent/team/claims/$2"; echo "$1 $p $(date +%s) $P.$1/tasks/$3" > "$P/.agent/team/claims/$2/owner"; }
sleep 900 & apid=$!; sleep 900 & cpid=$!

# a project whose plan is done; c holds 001 (pipeline/, tests/); 300 touches one test file
P=$T/p; mkdir -p "$P/tasks"; cd "$P"
git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
task 000-plan done "PLAN.md, tasks/"
task 001-fit-gate in-progress "pipeline/, tools/, tests/"
task 300-other open "tests/test_other.py"
git add -A && git commit -qm init
git worktree add -q "$P.a" -b agent/a && git worktree add -q "$P.c" -b agent/c
mkdir -p "$P/.agent/team/claims" "$P/.agent/team/loops" "$P.a/.agent" "$P.c/.agent"
echo $apid > "$P/.agent/team/loops/a.pid"; echo $cpid > "$P/.agent/team/loops/c.pid"
claim c 001 001-fit-gate.md

r=$(as a 'team_why_not tasks/300-other.md')
[ "$r" = "touches the same files as 001" ] && ok "while c builds 001, a task on its files waits" || bad "300 for a while 001 is built: '$r'"

# c's session asks the human and sets 001 blocked on its branch (ask_human)
sed -i '1s/.*/Status: blocked/' "$P.c/tasks/001-fit-gate.md"; git -C "$P.c" commit -qam "001: blocked on request 001"
r=$(as a 'team_why_not tasks/300-other.md')
[ -z "$r" ] && ok "once 001 is blocked on the human, its files are free for a" || bad "300 for a while 001 is blocked: '$r'"
r=$(as a 'for f in $(team_pick_order); do workable "$f" any && { echo "$f"; break; }; done')
[ "$r" = "tasks/300-other.md" ] && ok "300 is the first task a takes" || bad "a's first workable task: '$r'"
[ "$(cut -d' ' -f1 "$P/.agent/team/claims/001/owner")" = c ] && ok "c keeps its claim on 001" || bad "001's claim moved: $(cat "$P/.agent/team/claims/001/owner")"

# the human answers: the driver sets 001 back in progress on c's branch; new tasks on its files wait again
sed -i '1s/.*/Status: in-progress/' "$P.c/tasks/001-fit-gate.md"; git -C "$P.c" commit -qam "[driver] 001 resumed"
r=$(as a 'team_why_not tasks/300-other.md')
[ "$r" = "touches the same files as 001" ] && ok "after the answer, a task on 001's files waits again" || bad "300 for a after the answer: '$r'"
exit $fail
