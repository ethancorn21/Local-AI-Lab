#!/usr/bin/env bash
# test_team_idle.sh [DRIVER_DIR] : a team idling behind one task (agent-team-lib: team_idle_waiter, team_split_note,
# prep_stale, team_prep_target). frontpage 2026-10-07: every open task hung off 256, held by agent c for 12 sessions;
# a and b waited 168 and 221 minutes, with nothing to prepare because every waiting task had notes from 10-05, written
# before 233 was split and the dependencies rewired. Checked here:
#   - the split note: when another running agent has waited TEAM_IDLE_SPLIT_MIN for other agents and open tasks wait on
#     this one; not for a short wait, a wait for the human, a dead loop, a task nothing waits on, `Split: no`, during
#     planning, or with the rule off; the longest waiter is named; the size rule still comes first; never-both-ways
#   - prep: notes older than their task or a dependency in main are prepared again, after tasks with no notes at all;
#     the prompt says to rework the old notes; the refreshed file gets one stamp, not two
# Real git repo and worktrees; agents a, b, c are this shell's functions run with different AGENT_IDs. Run on the VM.
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/driver" && pwd)}
T=$(mktemp -d); trap 'kill $apid $bpid $cpid 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home PATH="$D:$PATH"; mkdir -p "$HOME/.agent-kit/agents"
printf 'LLM_URL=x\nTEAM_SPEED=4\n' > "$HOME/.agent-kit/agents/a.env"
printf 'LLM_URL=y\nTEAM_SPEED=1\nTEAM_MAX_TOUCHES=8\n' > "$HOME/.agent-kit/agents/b.env"
printf 'LLM_URL=z\nTEAM_SPEED=4\n' > "$HOME/.agent-kit/agents/c.env"
P=$T/frontpage; mkdir -p "$P/tasks" "$P/src"; cd "$P"
git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
task() { printf 'Status: %s\n# %s\n\nDepends on: %s\nTouches: %s\n\n## Acceptance criteria\n- [ ] works\n' "$2" "$1" "$3" "$4" > "tasks/$1.md"; }
at() { GIT_COMMITTER_DATE="@$1 +0000" GIT_AUTHOR_DATE="@$1 +0000" git -C "$P" commit -q "${@:2}"; }   # at <epoch> <commit args>
printf 'Status: done\n# 000\n' > tasks/000-plan.md
task 256-batch-endpoint in-progress none "src/rows.py, src/feed.py"   # rank 4: 256 -> 257 -> 233 -> 234
task 257-e2e-rows open 256 "e2e/rows.mjs"
task 233-feed-rows open "256, 257" "src/feed.html"
task 234-seen-strip open 233 "src/seen.py"
task 300-leaf open none "src/leaf.py"                                # nothing waits on it
task 301-wide in-progress none "w/1.py, w/2.py, w/3.py, w/4.py, w/5.py, w/6.py, w/7.py, w/8.py, w/9.py"   # 9 files > b's 8
git add -A && at 1000000000 -m init
git worktree add -q "$P.a" -b agent/a && git worktree add -q "$P.b" -b agent/b && git worktree add -q "$P.c" -b agent/c
mkdir -p "$P/.agent/team/claims" "$P/.agent/team/loops"
sleep 600 & apid=$!; sleep 600 & bpid=$!; sleep 600 & cpid=$!
echo $apid > "$P/.agent/team/loops/a.pid"; echo $bpid > "$P/.agent/team/loops/b.pid"; echo $cpid > "$P/.agent/team/loops/c.pid"
claim() { local p; eval "p=\$${1}pid"; mkdir -p "$P/.agent/team/claims/$2"; echo "$1 $p $(date +%s) $P.$1/tasks/$3" > "$P/.agent/team/claims/$2/owner"; }
state() { echo "$2 $(( $(date +%s) - $3 * 60 ))" > "$P/.agent/team/loops/$1.state"; }   # state <agent> <state> <minutes ago>
fns() {  # the definition of one function of agent-loop (one line or a block)
  awk -v n="$1" '!p && $0 ~ "^"n"\\(\\) *\\{" { print; if ($0 ~ /; }[[:space:]]*(#.*)?$/) exit; p = 1; next } p { print } p && /^}/ { exit }' "$D/agent-loop"
}
as() {  # as <agent> <commands> : in that agent's checkout, with its identity and the driver's helpers
  ( cd "$P.$1" && export TEAM_DIR=$P AGENT_ID=$1 HOME; TEAM_SPEED=$([ "$1" = b ] && echo 1 || echo 4)
    [ "$1" = b ] && export TEAM_MAX_TOUCHES=8
    eval "$(for f in status_of task_id handover_of pending_subtasks workable next_task commit_leftovers ask_field asks_with inbox_pending dead_status; do fns $f; done)"
    log() { echo "LOG $AGENT_ID $*" >> "$T/log"; }
    . "$D/agent-team-lib"; eval "$2" )
}
note() { as c "team_split_note tasks/$1.md"; }
claim c 256 256-batch-endpoint.md

# --- the split note when the team idles behind a task ---
[ -z "$(note 256-batch-endpoint)" ] && ok "nobody waits: no split" || bad "split with nobody waiting: $(note 256-batch-endpoint | cut -c1-120)"
state a wait 5; [ -z "$(note 256-batch-endpoint)" ] && ok "a waited 5 min: no split yet" || bad "split after 5 min"
state a wait 25; n=$(note 256-batch-endpoint)
[[ $n == " SPLIT THIS TASK FIRST: agent a has had nothing to build for 25 minutes, and other open tasks wait on this one"* ]] \
  && ok "a waited 25 min, three tasks wait on 256: c's session splits it" || bad "note: $(cut -c1-160 <<<"$n")"
grep -q 'Never both ways' <<<"$n" && grep -q 'tasks (the lowest free numbers in 800-1099) of at most 8 files each' <<<"$n" \
  && ok "the note forbids a cycle and keeps the parts small enough for b" || bad "note body: $(cut -c150-500 <<<"$n")"
state b wait 40; [[ $(note 256-batch-endpoint) == *"agent b has had nothing to build for 40 minutes"* ]] && ok "the longest waiter is named (b, 40 min)" || bad "names: $(note 256-batch-endpoint | cut -c1-90)"
[ -z "$(note 300-leaf)" ] && ok "a task nothing waits on: no split" || bad "leaf split: $(note 300-leaf | cut -c1-90)"
state a human 90; state b human 90; [ -z "$(note 256-batch-endpoint)" ] && ok "agents waiting for the human, not for c: no split" || bad "split for a human wait"
state a wait 30; kill $apid; wait $apid 2>/dev/null
[ -z "$(note 256-batch-endpoint)" ] && ok "the waiter's loop is gone: no split" || bad "split for a dead loop"
sleep 600 & apid=$!; echo $apid > "$P/.agent/team/loops/a.pid"
[ -n "$(note 256-batch-endpoint)" ] && ok "(a running again: the note is back)" || bad "no note with a waiting again"
[ -z "$(as c 'TEAM_IDLE_SPLIT_MIN=0 team_split_note tasks/256-batch-endpoint.md')" ] && ok "TEAM_IDLE_SPLIT_MIN=0: the rule is off" || bad "rule not off"
sed -i '1s/.*/Status: in-progress/' "$P/tasks/000-plan.md"; [ -z "$(note 256-batch-endpoint)" ] && ok "planning open in main: no split" || bad "split during planning"
sed -i '1s/.*/Status: done/' "$P/tasks/000-plan.md"
sed -i 's/^Touches:.*/&\nSplit: no - one template and its endpoint/' "$P.c/tasks/256-batch-endpoint.md"
[ -z "$(note 256-batch-endpoint)" ] && ok "Split: no in the task: no split" || bad "split despite Split: no"
sed -i '/^Split: no/d' "$P.c/tasks/256-batch-endpoint.md"
state a work 0; state b work 0; n=$(as c 'team_split_note tasks/301-wide.md')
[[ $n == " SPLIT THIS TASK FIRST: it changes 9 files, and another agent can only take tasks of at most 8 files"* ]] && ok "size rule unchanged: 9 files > b's 8, nobody waiting" || bad "size note: $(cut -c1-120 <<<"$n")"
# the driver's log line and event take the reason from the note
state a wait 25; split=$(note 256-batch-endpoint); swhy=${split#" SPLIT THIS TASK FIRST: "}; swhy=${swhy%%. Split what*}
[ "$swhy" = "agent a has had nothing to build for 25 minutes, and other open tasks wait on this one, so as one task it keeps them waiting for you alone" ] \
  && grep -qF 'swhy=${split#" SPLIT THIS TASK FIRST: "}; swhy=${swhy%%. Split what*}' "$D/agent-loop" && ok "agent-loop logs the reason" || bad "reason: $swhy"

# --- stale prep notes ---
state a wait 30; state b wait 30
mkdir -p tasks/prep; for i in 233 234; do echo "# old notes $i" > tasks/prep/$i.md; done
git add tasks/prep && at 1000000100 -m "[driver] prep notes 233 234 (10-05)"
sed -i 's/^Depends on: 233$/Depends on: 233, 256/' tasks/234-seen-strip.md; git add tasks && at 1000000200 -m "[driver] 233 split by agent a"
for x in a b c; do git -C "$P.$x" merge -q --no-edit main > /dev/null 2>&1; done
as b 'prep_stale tasks/234-seen-strip.md' && ok "234's notes predate its task's change in main: stale" || bad "234 not stale"
as b 'prep_stale tasks/233-feed-rows.md' && bad "233 stale with nothing changed" || ok "233's notes newer than 233, 256 and 257: fresh"
t=$(as b team_prep_target); [ "$t" = tasks/257-e2e-rows.md ] && ok "b prepares 257 (no notes) before refreshing 234" || bad "first target: $t"
echo "# notes 257" > tasks/prep/257.md; git add tasks/prep && at 1000000300 -m "[driver] prep notes 257"; git -C "$P.b" merge -q --no-edit main > /dev/null 2>&1
t=$(as b team_prep_target); [ "$t" = tasks/234-seen-strip.md ] && ok "nothing unprepared left: b refreshes 234's stale notes" || bad "refresh target: $t"
as b 'team_prep_prompt tasks/234-seen-strip.md 9' | grep -q 'tasks/prep/234.md already holds notes from an earlier prep' && ok "refresh prompt: rework the old notes, replace the file" || bad "no refresh wording"
! as b 'team_prep_prompt tasks/300-leaf.md 9' | grep -q 'already holds notes' || bad "refresh wording on a task without notes"
sed -i '1s/.*/Status: done/' tasks/257-e2e-rows.md; git add tasks && at 1000000400 -m "257 done"; git -C "$P.b" merge -q --no-edit main > /dev/null 2>&1
as b 'prep_stale tasks/233-feed-rows.md' && ok "a dependency (257) finished in main: 233's notes are stale too" || bad "233 not stale after 257 changed"
[ -z "$(as b 'prep_stale tasks/300-leaf.md' && echo y)" ] && ok "no notes in main: not stale (prepared as new)" || bad "no-notes stale"
# a refresh that changes nothing is counted like an empty prep (else the stale notes are picked forever)
cd "$P.b"; as b 'prep_lock tasks/234-seen-strip.md'; h0=$(git rev-parse HEAD)
as b "team_prep_finish tasks/234-seen-strip.md $h0 'old basis'"
[ "$(cat "$P/.agent/team/prep-tries/234" 2>/dev/null)" = 1 ] && [ "$(git -C "$P" show main:tasks/prep/234.md)" = "# old notes 234" ] \
  && ok "refresh left the notes as they were: counted (prep-tries/234 = 1), main untouched" || bad "unchanged refresh: tries $(cat "$P/.agent/team/prep-tries/234" 2>/dev/null)"
# a refresh keeps one stamp
printf '> Prep notes by agent a, 2026-10-05 00:32, written old basis\n> Check every assumption against main before building on it.\n\n# new notes 234\n' > tasks/prep/234.md
git add tasks/prep/234.md && git commit -qm "prep 234: refreshed"
as b "team_prep_finish tasks/234-seen-strip.md $h0 '2 step(s) before it could start; built on: 233'"
s=$(git -C "$P" show main:tasks/prep/234.md)
[ "$(grep -c '^> Prep notes by agent' <<<"$s")" = 1 ] && grep -q '^> Prep notes by agent b, .*built on: 233' <<<"$s" && grep -q '^# new notes 234' <<<"$s" \
  && ok "refreshed notes in main: one stamp (b's), the new text" || bad "stamp: $(head -4 <<<"$s" | tr '\n' '|')"
cd "$P"; git -C "$P.b" merge -q --no-edit main > /dev/null 2>&1
as b 'prep_stale tasks/234-seen-strip.md' && bad "234 still stale after its refresh" || ok "after the refresh 234 is fresh again"
exit $fail
