#!/usr/bin/env bash
# test_team_replan_overlap.sh [DRIVER_DIR] : a re-plan is not kept back by another agent's files (agent-team-lib,
# team_why_not). ancient-works 2026-10-10: GOAL.md changed and 000 was reopened on agent a's branch; its Touches line
# (PLAN.md, tasks/, tests/, ...) overlapped agent c's 001 (pipeline/, tests/), then also 200, which c took meanwhile,
# and a wrote prep notes for eight tasks under the old plan for two hours. Checked here:
#   - the reopened 000 is free for a while c holds a task on the same files, and is the first task a would take
#   - any other task on those files still waits for c (the overlap rule stands for the rest)
#   - once a holds 000, c's new tasks on its files wait for the re-plan; c's own claim is untouched
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

# a project whose plan is done in main; c holds 001; GOAL.md changed and the lead agent a reopened 000 on its branch
P=$T/p; mkdir -p "$P/tasks"; cd "$P"
git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
task 000-plan done "PLAN.md, tasks/, requirements.txt, tests/"
task 001-fit-gate in-progress "pipeline/, tools/, tests/"
task 300-other open "tests/test_other.py"
git add -A && git commit -qm init
git worktree add -q "$P.a" -b agent/a && git worktree add -q "$P.c" -b agent/c
mkdir -p "$P/.agent/team/claims" "$P/.agent/team/loops" "$P.a/.agent" "$P.c/.agent"
echo $apid > "$P/.agent/team/loops/a.pid"; echo $cpid > "$P/.agent/team/loops/c.pid"
claim c 001 001-fit-gate.md
sed -i '1s/.*/Status: in-progress/' "$P.a/tasks/000-plan.md"; git -C "$P.a" commit -qam "[driver] GOAL.md changed: tasks/000-plan.md reopened"

r=$(as a 'team_why_not tasks/000-plan.md')
[ -z "$r" ] && ok "the reopened 000 is free for a while c holds 001 on the same files" || bad "000 for a: '$r'"
r=$(as a 'team_why_not tasks/300-other.md')
[ "$r" = "touches the same files as 001" ] && ok "another task on c's files still waits for c" || bad "300 for a: '$r'"
r=$(as a 'for f in $(team_pick_order); do team_workable "$f" && { echo "$f"; break; }; done')
[ "$r" = "tasks/000-plan.md" ] && ok "000 is the first task a takes" || bad "a's first workable task: '$r'"

# a takes 000: c's new tasks on its files wait for the re-plan; c's own claim goes on
claim a 000 000-plan.md
r=$(as c 'team_why_not tasks/300-other.md')
[ "$r" = "touches the same files as 000" ] && ok "while a re-plans, c's new task on its files waits" || bad "300 for c: '$r'"
r=$(as c 'team_why_not tasks/001-fit-gate.md')
[ -z "$r" ] && ok "c's own 001 is untouched" || bad "001 for c: '$r'"
exit $fail
