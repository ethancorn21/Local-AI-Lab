#!/usr/bin/env bash
# test_team_sync_stale.sh [DRIVER_DIR] : a sync conflict that no session would ever resolve (agent-team-lib, Sync).
# frontpage 2026-10-10: agent b's loop stopped STUCK on 294 with its research notes in the task file; agent a took the
# stale claim and finished 294 in main. From b's restart every sync conflicted on that one file and was left to "the next
# session", but b, with nothing workable in its stale view of the tasks, never had one: 325 identical conflicts in 10
# hours, 766 commits behind main, holding a claim (318) on a task its checkout could not see. Checked here:
#   - a task file main has finished (done or dropped) and this agent does not hold takes main's version, the merge
#     completes, the task's other work leaves the branch, the branch's own notes stay in its history, and the agent sees
#     (and can take) the tasks that exist only in main; also when the stale copy says in-progress (a loop killed
#     mid-session) and came in an unattributed leftover commit
#   - not when this agent holds the task's claim, nor when any other file conflicts too (the merge is aborted, as before)
#   - a conflict that stays is logged once, not once a minute
#   - an agent that waits holding no claim, with a conflict the rule cannot resolve: its branch is backed up and reset to
#     main; with a claim held it is left for the claim's next session
# Real git repo and worktrees; agents a and b are this shell's functions run with different AGENT_IDs. Run on the VM.
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/driver" && pwd)}
T=$(mktemp -d); trap 'kill $apid $bpid 2>/dev/null; rm -rf "$T"' EXIT
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
events() { grep -c "\"event\":\"$1\"" "$P/.agent/team/events.jsonl" 2>/dev/null || true; }
behind() { ! git -C "$P.$1" merge-base --is-ancestor main HEAD; }
merging() { git -C "$P.$1" rev-parse -q --verify MERGE_HEAD > /dev/null; }
setup() {  # a fresh project: b worked on 294 (notes in its task file); a then finished 294 in main and added 318
  rm -rf "$T/p"* "$T/log"; P=$T/p; mkdir -p "$P/tasks" "$P/src"; cd "$P"
  git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
  printf 'Status: done\n# 000\n' > tasks/000-plan.md
  task 294-talks in-progress "e2e/talks.mjs"; echo "x = 1" > src/other.py
  git add -A && git commit -qm init
  git worktree add -q "$P.a" -b agent/a && git worktree add -q "$P.b" -b agent/b
  mkdir -p "$P/.agent/team/claims" "$P/.agent/team/loops" "$P.a/.agent" "$P.b/.agent"
  echo $apid > "$P/.agent/team/loops/a.pid"; echo $bpid > "$P/.agent/team/loops/b.pid"
  printf '\n## Hand-over\nresearch pass 1: design recorded, no code yet\n' >> "$P.b/tasks/294-talks.md"
  mkdir -p "$P.b/e2e"; echo "// b's first try" > "$P.b/e2e/talks_try.mjs"
  git -C "$P.b" add -A && git -C "$P.b" commit -qm "294: hand-over - research pass 1"; NOTES=$(git -C "$P.b" rev-parse HEAD)
  # a took b's stale claim and finished 294; the claim was released when it went into main
  mkdir -p e2e; echo "// a's finished spec" > e2e/talks.mjs
  sed -i '1s/.*/Status: done/' tasks/294-talks.md; printf '\n## Hand-over\nclosed by agent a\n' >> tasks/294-talks.md
  task 318-golden-spec open "e2e/golden.mjs"
  git add -A && git commit -qm "294: done (agent a); 318 split off"
}
sleep 900 & apid=$!; sleep 900 & bpid=$!

# --- b's stale copy says open: main's finished version is taken, b sees 318 ---
setup
sed -i '1s/.*/Status: open/' "$P.b/tasks/294-talks.md"; git -C "$P.b" commit -qam "[driver] uncommitted work found at loop start"
as b 'team_sync; echo "rc=$? conflict=[$SYNC_CONFLICT]"' > "$T/out"
grep -q '^rc=0 conflict=\[\]$' "$T/out" && ! behind b && ok "open copy: the sync merges main" || bad "open copy: $(cat "$T/out") behind=$(behind b && echo yes)"
cmp -s "$P.b/tasks/294-talks.md" <(git -C "$P" show main:tasks/294-talks.md) && ok "294 on b's branch is main's finished version" || bad "294 on b: $(head -1 "$P.b/tasks/294-talks.md")"
[ ! -e "$P.b/e2e/talks_try.mjs" ] && [ "$(cat "$P.b/e2e/talks.mjs")" = "// a's finished spec" ] \
  && ok "294's other work (e2e/talks_try.mjs) left b's branch; a's spec is there" || bad "e2e on b: $(ls "$P.b/e2e")"
git -C "$P.b" merge-base --is-ancestor "$NOTES" HEAD && ok "b's notes stay in its branch's history" || bad "b's notes commit lost"
[ "$(events sync_took_main)" = 1 ] && [ "$(events sync_conflict)" = 0 ] && grep -q "LOG b team: .*294" "$T/log" \
  && ok "one sync_took_main event and a log line, no sync_conflict" || bad "events: took_main $(events sync_took_main), conflict $(events sync_conflict)"
[ -f "$P.b/tasks/318-golden-spec.md" ] && [ -z "$(as b 'team_why_not tasks/318-golden-spec.md')" ] \
  && ok "b sees 318, which exists only in main, and nothing keeps it from b" || bad "318 on b: $(ls "$P.b/tasks")"

# --- in-progress copy (a loop killed mid-session) in an unattributed leftover commit: resolved too ---
setup
sed -i '1s/.*/Status: in-progress/' "$P.b/tasks/294-talks.md"; echo "more notes" >> "$P.b/tasks/294-talks.md"
git -C "$P.b" commit -qam "[driver] uncommitted work found at loop start"
as b 'team_sync; echo "rc=$?"' > "$T/out"
grep -qx 'rc=0' "$T/out" && ! behind b && [ "$(head -1 "$P.b/tasks/294-talks.md")" = "Status: done" ] \
  && ok "in-progress copy: main's done version taken, merge complete" || bad "in-progress copy: $(cat "$T/out"), status $(head -1 "$P.b/tasks/294-talks.md")"

# --- b holds 294's claim: its copy is its own work, left to its next session ---
setup
claim b 294 294-talks.md
h=$(git -C "$P.b" rev-parse HEAD)
as b 'team_sync; echo "rc=$? conflict=[$SYNC_CONFLICT]"' > "$T/out"
grep -q '^rc=1 conflict=\[tasks/294-talks.md' "$T/out" && [ "$(git -C "$P.b" rev-parse HEAD)" = "$h" ] && ! merging b && [ -z "$(git -C "$P.b" status --porcelain)" ] \
  && ok "own claim: conflict left for the session, branch unchanged" || bad "own claim: $(cat "$T/out")"
# ... and waiting does not reset a branch that holds a claim
as b 'team_wait > /dev/null 2>&1; echo "conflict=[$SYNC_CONFLICT]"' > "$T/out"
[ "$(git -C "$P.b" rev-parse HEAD)" = "$h" ] && [ "$(events sync_reset)" = 0 ] && ok "own claim: a wait leaves the branch as it was" || bad "own claim wait: HEAD moved or reset ($(events sync_reset))"
rm -rf "$P/.agent/team/claims/294"

# --- another file conflicts too: aborted as before; logged once while it stays ---
setup
echo "x = 2  # b" > "$P.b/src/other.py"; git -C "$P.b" commit -qam "[driver] uncommitted work found at loop start"
echo "x = 3  # a" > src/other.py; git commit -qam "300: other"
h=$(git -C "$P.b" rev-parse HEAD)
as b 'team_sync; echo "rc=$? conflict=[$SYNC_CONFLICT]"; team_sync; team_sync' > "$T/out"
grep -q '^rc=1 conflict=\[.*src/other.py' "$T/out" && grep -q 'tasks/294-talks.md' "$T/out" && [ "$(git -C "$P.b" rev-parse HEAD)" = "$h" ] \
  && ! merging b && ok "a code file conflicts too: merge aborted, branch unchanged, both files reported" || bad "mixed: $(cat "$T/out")"
[ "$(events sync_conflict)" = 1 ] && ok "three syncs with the same conflict: one sync_conflict event" || bad "sync_conflict events: $(events sync_conflict)"

# --- b waits holding no claim, with that unresolvable conflict: backed up and reset to main ---
as b 'team_wait > /dev/null 2>&1; echo "conflict=[$SYNC_CONFLICT]"' > "$T/out"
bk=$(git -C "$P" branch --list 'agent/b-sync-*' --format='%(refname:short)' | head -1)
! behind b && [ "$(git -C "$P.b" rev-parse HEAD)" = "$(git -C "$P" rev-parse main)" ] && grep -qx 'conflict=\[\]' "$T/out" \
  && ok "no claim, waiting: b's branch reset to main" || bad "no-claim wait: $(cat "$T/out") behind=$(behind b && echo yes)"
[ -n "$bk" ] && [ "$(git -C "$P" rev-parse "$bk")" = "$h" ] && [ "$(events sync_reset)" = 1 ] \
  && ok "the old branch is kept on $bk, one sync_reset event" || bad "backup: '$bk', sync_reset $(events sync_reset)"
exit $fail
