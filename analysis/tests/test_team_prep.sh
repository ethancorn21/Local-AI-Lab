#!/usr/bin/env bash
# test_team_prep.sh [DRIVER_DIR] : critical-path picking and prep sessions in team mode (agent-team-lib).
# frontpage 2026-10-04: every open task hung off 230; agent b (a quarter of a's speed) waited 862 minutes over the
# project, and lowest-number-first would next have handed b the module split six tasks wait on. Checked here:
#   - pick order: the fast agent takes the longest chain, a slower one (while a faster runs) the tasks nothing waits on
#   - the prep target: unclaimed, not prepared, waiting only on work being built or startable, not too big
#   - the prep lock; a prep session keeps only its notes, which go into main; an empty one is counted
#   - the cut: once the task can be built (or other work is free) the session is signalled to end
#   - the hand-off: an agent that claims a task being prepared waits for the notes, and gets them
#   - the planning task is not taken again from a checkout that is only behind main (the slower pick widened that race)
#   - the doorbell rings once per set of open requests (a team agent comes back to that wait after every prep session)
# Real git repo and worktrees; agents a and b are this shell's functions run with different AGENT_IDs. Run on the VM.
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/driver" && pwd)}
T=$(mktemp -d); trap 'kill $apid $bpid 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home PATH="$D:$PATH"; mkdir -p "$HOME/.agent-kit/agents"
printf 'LLM_URL=x\nTEAM_SPEED=4\n' > "$HOME/.agent-kit/agents/a.env"
printf 'LLM_URL=y\nTEAM_SPEED=1\nTEAM_MAX_TOUCHES=2\n' > "$HOME/.agent-kit/agents/b.env"
P=$T/proj; mkdir -p "$P/tasks" "$P/src"; cd "$P"
git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
task() { printf 'Status: %s\n# %s\n\nDepends on: %s\nTouches: %s\n\n## Acceptance criteria\n- [ ] works\n' "$2" "$1" "$3" "$4" > "tasks/$1.md"; }
printf 'Status: done\n# 000\n' > tasks/000-plan.md
task 101-leaf open none "src/l.py"                     # rank 1
task 102-root open none "src/r.py"                     # rank 3: 102 -> 103 -> 104
task 103-mid open 102 "src/m.py"
task 104-end open 103 "src/e.py"
task 105-side open none "src/s.py"                     # rank 2: 105 -> 106
task 106-after-side open 105 "src/a.py"
task 107-big open 102 "src/b1.py, src/b2.py, src/b3.py"   # too big for b to build (3 files > 2) and to read (25 KB)
task 108-wide open 102 "src/x.py, src/y.py, src/z.py"     # too big for b to build (3 files), small to read: b prepares it
echo base > src/x.py; echo y > src/y.py; echo z > src/z.py; head -c 25000 /dev/zero | tr '\0' x > src/b1.py
git add -A && git commit -q -m init
git worktree add -q "$P.a" -b agent/a && git worktree add -q "$P.b" -b agent/b
mkdir -p "$P/.agent/team/claims" "$P/.agent/team/loops"
sleep 600 & apid=$!; sleep 600 & bpid=$!
echo $apid > "$P/.agent/team/loops/a.pid"; echo $bpid > "$P/.agent/team/loops/b.pid"
claim() { mkdir -p "$P/.agent/team/claims/$2"; echo "$1 $([ "$1" = a ] && echo $apid || echo $bpid) $(date +%s) $P.$1/tasks/$3" > "$P/.agent/team/claims/$2/owner"; }
unclaim() { rm -rf "$P/.agent/team/claims/$1"; }
fns() {  # the definition of one function of agent-loop (one line or a block)
  awk -v n="$1" '!p && $0 ~ "^"n"\\(\\) *\\{" { print; if ($0 ~ /; }[[:space:]]*(#.*)?$/) exit; p = 1; next } p { print } p && /^}/ { exit }' "$D/agent-loop"
}
as() {  # as <agent> <commands> : in that agent's checkout, with its identity and the driver's helpers
  ( cd "$P.$1" && export TEAM_DIR=$P AGENT_ID=$1 HOME TEAM_SPEED; TEAM_SPEED=$([ "$1" = a ] && echo 4 || echo 1)
    [ "$1" = b ] && export TEAM_MAX_TOUCHES=2 WRAPUP_SOFT_TOKENS=30000   # b reads at most (30000 - 25000) * 4 = 20 KB in prep
    eval "$(for f in status_of task_id handover_of pending_subtasks workable next_task commit_leftovers ask_field asks_with inbox_pending wait_for_human; do fns $f; done)"
    log() { echo "LOG $AGENT_ID $*" >> "$T/log"; }
    . "$D/agent-team-lib"; eval "$2" )
}
ev() { grep -c "\"event\":\"$1\"" "$P/.agent/team/events.jsonl" 2>/dev/null || echo 0; }

# --- pick order ---
[ "$(as a next_task)" = tasks/102-root.md ] && ok "fast agent a takes the longest chain (102, rank 3)" || bad "a took $(as a next_task)"
[ "$(as b next_task)" = tasks/101-leaf.md ] && ok "slow agent b, a running, takes a task nothing waits on (101)" || bad "b took $(as b next_task)"
kill $apid; wait $apid 2>/dev/null
[ "$(as b next_task)" = tasks/102-root.md ] && ok "b alone (a stopped) takes the longest chain itself" || bad "b alone took $(as b next_task)"
sleep 600 & apid=$!; echo $apid > "$P/.agent/team/loops/a.pid"
sed -i 's/^TEAM_SPEED=.*/TEAM_SPEED=4/' "$HOME/.agent-kit/agents/b.env"
[ "$(as b 'TEAM_SPEED=4; next_task')" = tasks/102-root.md ] && ok "equal speeds: both take the longest chain" || bad "equal speeds: b took $(as b 'TEAM_SPEED=4; next_task')"
sed -i 's/^TEAM_SPEED=.*/TEAM_SPEED=1/' "$HOME/.agent-kit/agents/b.env"

# --- prep target ---
claim a 102 102-root.md; claim b 105 105-side.md
[ "$(as b team_prep_target)" = tasks/103-mid.md ] && ok "prep target: 103 (waits only on 102, which a builds; longest chain)" || bad "target: $(as b team_prep_target)"
mkdir -p "$P.b/tasks/prep"; echo notes > "$P.b/tasks/prep/103.md"
t=$(as b team_prep_target); [ "$t" = tasks/106-after-side.md ] && ok "103 prepared: nearest first, 106 (1 step) before 104 (2 steps)" || bad "after 103 prepared: $t"
echo notes > "$P.b/tasks/prep/106.md"
t=$(as b team_prep_target); [ "$t" = tasks/108-wide.md ] && ok "108: over b's build limit (3 files > 2) but small to read, so b prepares it" || bad "wide target: $t"
echo notes > "$P.b/tasks/prep/108.md"
t=$(as b team_prep_target); [ "$t" = tasks/104-end.md ] && ok "nothing nearer left: 104, two steps out (waits on 103, which waits on 102)" || bad "deeper target: $t"
[ "$(as b 'prep_layers tasks/104-end.md')" = 2 ] && [ "$(as b 'prep_layers tasks/103-mid.md')" = 1 ] && ok "layers: 103 = 1, 104 = 2" || bad "layers: 103 $(as b 'prep_layers tasks/103-mid.md'), 104 $(as b 'prep_layers tasks/104-end.md')"
basis=$(as b 'team_prep_basis tasks/104-end.md')
[[ $basis == "2 step(s) before it could start; built on: 103 (103-mid: not started yet, prep notes tasks/prep/103.md);" ]] && ok "basis names the unbuilt dependency and its prep notes" || bad "basis: $basis"
as b 'team_prep_prompt tasks/104-end.md 9' | grep -q 'This task is 2 steps from starting' && ok "deep prep prompt: says it builds on guesses, plan at the level that survives" || bad "no deep-prep wording"
! as b 'team_prep_prompt tasks/103-mid.md 9' | grep -q 'steps from starting' && ok "one step out: no deep-prep wording" || bad "deep wording on a 1-step task"
echo notes > "$P.b/tasks/prep/104.md"
t=$(as b team_prep_target); [ -z "$t" ] && ok "b: 107 (25 KB to read, over its 20 KB prep budget) is left, nothing else" || bad "b too-big: $t"
t=$(as a 'mkdir -p tasks/prep; echo n > tasks/prep/103.md; echo n > tasks/prep/106.md; team_prep_target; rm -rf tasks/prep')
[ "$t" = tasks/107-big.md ] && ok "a (bigger window): 107 is its target" || bad "a target: $t"
rm -rf "$P.b/tasks/prep"
claim a 103 103-mid.md
t=$(as b team_prep_target); [ "$t" = tasks/104-end.md ] && ok "a claims 103: 104 (waits only on work in progress) is b's target" || bad "with 103 claimed: $t"
unclaim 103
sed -i '1s/.*/Status: open/' "$P/tasks/000-plan.md"
[ -z "$(as b team_prep_target)" ] && ok "planning open in main: no prep (the plan may change every task)" || bad "prep during planning: $(as b team_prep_target)"
sed -i '1s/.*/Status: done/' "$P/tasks/000-plan.md"
grep -q . <<<"$(as b 'team_prep_prompt tasks/103-mid.md 7')" && as b 'team_prep_prompt tasks/103-mid.md 7' | grep -q '102 (102-root: being built by agent a, branch agent/a)' \
  && as b 'team_prep_prompt tasks/103-mid.md 7' | grep -q 'Write tasks/prep/103.md' && ok "prep prompt: names the notes file and the branch the dependency is built on" || bad "prompt: $(as b 'team_prep_prompt tasks/103-mid.md 7' | cut -c1-200)"

# --- lock ---
as b 'prep_lock tasks/103-mid.md' && ok "b takes the prep lock on 103" || bad "b could not lock 103"
! as a 'prep_lock tasks/103-mid.md' && ok "a cannot take it while b's loop runs" || bad "a took b's prep lock"
[ "$(as a 'prep_owner 103')" = b ] && ok "prep_owner 103 = b" || bad "owner: $(as a 'prep_owner 103')"
[ "$(as b team_prep_target)" != tasks/103-mid.md ] && ok "a task being prepared is not a target again" || bad "103 targeted twice"
echo "b 999999 0" > "$P/.agent/team/prep/103/owner"
as a 'prep_lock tasks/103-mid.md' && [ "$(as b 'prep_owner 103')" = a ] && ok "a lock whose loop is gone is taken over" || bad "stale lock not taken over"
rm -rf "$P/.agent/team/prep/103"

# --- a prep session keeps only its notes, and they go into main ---
cd "$P.b"; as b 'prep_lock tasks/103-mid.md'; h0=$(git rev-parse HEAD)
mkdir -p tasks/prep; printf '# prep 103\nassumption: r.py exports root()\n' > tasks/prep/103.md
echo changed > src/x.py; echo new > src/new.py; echo "agent edit" >> tasks/103-mid.md; echo "progress" > PROGRESS.md
git add -A && git commit -q -m "prep 103: notes (and things it should not have)"
echo "uncommitted" >> src/x.py
as b "team_prep_finish tasks/103-mid.md $h0 '1 step(s) before it could start; built on: 102 (102-root: being built by agent a, branch agent/a);'"
[ "$(git diff --name-only "$h0" HEAD)" = tasks/prep/103.md ] && ok "only tasks/prep/103.md changed on b's branch (code, task file, PROGRESS.md put back)" || bad "branch diff: $(git diff --name-only "$h0" HEAD | tr '\n' ' ')"
[ "$(git rev-list --count "$h0..HEAD")" = 1 ] && [ "$(git log -1 --format=%s)" = "[driver] prep 103: notes by agent b" ] \
  && ok "the branch: one commit with the notes on top of the session start (the session's own commits are gone)" || bad "branch history: $(git log --oneline "$h0..HEAD" | tr '\n' '|')"
[ "$(cat src/x.py)" = base ] && [ ! -e src/new.py ] && [ -z "$(git status --porcelain)" ] && ok "worktree clean, src/x.py back to base, src/new.py gone" || bad "worktree: $(git status --porcelain | tr '\n' ' ')"
git -C "$P" cat-file -e main:tasks/prep/103.md 2>/dev/null && [ -z "$(git -C "$P" status --porcelain)" ] && ok "notes published to main, main clean" || bad "notes not in main"
git -C "$P" show main:tasks/prep/103.md | head -1 | grep -q '^> Prep notes by agent b, .*, written 1 step(s) before it could start; built on: 102 (102-root: being built by agent a' \
  && git -C "$P" show main:tasks/prep/103.md | grep -q '^# prep 103' && ok "notes stamped with what they were built on, the agent's text kept" || bad "stamp: $(git -C "$P" show main:tasks/prep/103.md | head -2 | tr '\n' '|')"
! git -C "$P" cat-file -e main:src/new.py 2>/dev/null && ok "no code went into main" || bad "code in main"
[ ! -d "$P/.agent/team/prep/103" ] && [ "$(ev prep_published)" -ge 1 ] && ok "lock freed, prep_published logged" || bad "lock/event"
as b 'prep_lock tasks/106-after-side.md'; h0=$(git rev-parse HEAD)
as b "team_prep_finish tasks/106-after-side.md $h0"
[ "$(cat "$P/.agent/team/prep-tries/106" 2>/dev/null)" = 1 ] && [ "$(ev prep_empty)" = 1 ] && ok "a prep session without notes is counted (prep-tries/106 = 1)" || bad "empty prep not counted"
echo 2 > "$P/.agent/team/prep-tries/106"; [ "$(as b team_prep_target)" != tasks/106-after-side.md ] && ok "two empty preps: 106 is not prepared again" || bad "106 retried"
cd "$P"

# --- the cut: prep ends once the task can be built ---
git -C "$P.b" merge -q --no-edit main > /dev/null 2>&1
claim a 101 101-leaf.md; claim a 102 102-root.md; claim a 105 105-side.md   # nothing is free for b
! as b 'prep_ready tasks/107-big.md' && ok "107 not ready while a builds 102" || bad "107 ready too early"
[ -z "$(as b next_task)" ] && ok "set-up: nothing is free for b" || bad "b could take $(as b next_task)"
as b 'prep_lock tasks/104-end.md'; mkdir -p "$P.b/.agent"; rm -f "$P.b/.agent/wrapup-now"
( as b 'TEAM_PREP_POLL_S=1 team_prep_watch tasks/104-end.md' ) & w=$!
sleep 3; [ ! -f "$P.b/.agent/wrapup-now" ] && ok "no signal while 104 waits on 103" || bad "signalled too early: $(cat "$P.b/.agent/wrapup-now")"
sed -i '1s/.*/Status: done/' "$P/tasks/102-root.md" "$P/tasks/103-mid.md"; git -C "$P" commit -qam "102, 103 done"; unclaim 102
for _ in $(seq 1 8); do [ -f "$P.b/.agent/wrapup-now" ] && break; sleep 1; done
grep -qE 'task 104 (can be built now|is free for you to build)' "$P.b/.agent/wrapup-now" 2>/dev/null && ok "dependencies in main: the prep session is signalled to end" || bad "no signal: $(cat "$P.b/.agent/wrapup-now" 2>/dev/null)"
wait $w 2>/dev/null; rm -f "$P.b/.agent/wrapup-now"; rm -rf "$P/.agent/team/prep/104"
# other work free for this agent ends prep too (real work beats prep)
sed -i '1s/.*/Status: open/' "$P/tasks/102-root.md" "$P/tasks/103-mid.md"; git -C "$P" commit -qam "reopen"; claim a 102 102-root.md
as b 'prep_lock tasks/107-big.md'
( as b 'TEAM_PREP_POLL_S=1 team_prep_watch tasks/107-big.md' ) & w=$!
sleep 3; [ ! -f "$P.b/.agent/wrapup-now" ] && ok "no signal while nothing is free" || bad "signalled: $(cat "$P.b/.agent/wrapup-now")"
unclaim 105
for _ in $(seq 1 8); do [ -f "$P.b/.agent/wrapup-now" ] && break; sleep 1; done
grep -q 'task 105 is free for you to build' "$P.b/.agent/wrapup-now" 2>/dev/null && ok "105 freed: prep is cut (real work beats prep)" || bad "not cut for free work: $(cat "$P.b/.agent/wrapup-now" 2>/dev/null)"
wait $w 2>/dev/null; rm -f "$P.b/.agent/wrapup-now"; rm -rf "$P/.agent/team/prep/107"; unclaim 101

# --- the hand-off: a claims a task b is still preparing ---
sleep 600 & ppid=$!; mkdir -p "$P/.agent/team/prep/106"; echo "b $ppid $(date +%s)" > "$P/.agent/team/prep/106/owner"
( sleep 3; mkdir -p "$P/tasks/prep"; echo "# prep 106" > "$P/tasks/prep/106.md"; git -C "$P" add tasks/prep/106.md
  git -C "$P" commit -qm "[driver] prep notes for 106 by agent b"; rm -rf "$P/.agent/team/prep/106"; kill $ppid ) &
t0=$(date +%s); as a 'TEAM_PREP_HANDOFF_POLL_S=1 team_prep_handoff tasks/106-after-side.md'; s=$(( $(date +%s) - t0 ))
[ "$s" -ge 2 ] && [ "$s" -le 15 ] && [ -f "$P.a/tasks/prep/106.md" ] && ok "a waited ${s}s for b's notes on 106 and has them" || bad "hand-off: ${s}s, notes $([ -f "$P.a/tasks/prep/106.md" ] && echo yes || echo no)"
grep -q '"prep_handoff".*notes received' "$P/.agent/team/events.jsonl" && ok "prep_handoff logged with notes received" || bad "no prep_handoff event"
# race: notes reached main after a's last sync, lock already gone -> the hand-off syncs anyway
( cd "$P" && echo "# prep 101" > tasks/prep/101.md && git add tasks/prep/101.md && git commit -qm "[driver] prep notes for 101" )
as a 'team_prep_handoff tasks/101-leaf.md'
[ -f "$P.a/tasks/prep/101.md" ] && ok "notes published just before the claim: the hand-off syncs them" || bad "101 notes missing in a's checkout"

# --- the claimer's prompt points at the notes ---
n=$(as a 'team_prep_note tasks/106-after-side.md'); [[ $n == " PREP NOTES: tasks/prep/106.md"* ]] && ok "first session: PREP NOTES pointer with the check-first instruction" || bad "note: $n"
printf '\n## Hand-over\nbuilt half\n' >> "$P.a/tasks/106-after-side.md"
n=$(as a 'team_prep_note tasks/106-after-side.md'); [ "$n" = " Prep notes for this task: tasks/prep/106.md." ] && ok "after a hand-over: a short pointer" || bad "note after hand-over: $n"
[ -z "$(as a 'team_prep_note tasks/105-side.md')" ] && ok "no notes: no pointer" || bad "pointer without notes"

# --- the planning task from a stale view: b synced while 000 was open, then a merged the finished plan ---
( cd "$P" && sed -i '1s/.*/Status: open/' tasks/000-plan.md && git commit -qam "000 open (planning)" )
git -C "$P.b" merge -q --no-edit main > /dev/null 2>&1
( cd "$P" && sed -i '1s/.*/Status: done/' tasks/000-plan.md && git commit -qam "000 done (a's plan merged)" )
! as b 'claim_task tasks/000-plan.md' && [ ! -d "$P/.agent/team/claims/000" ] && [ "$(head -1 "$P.b/tasks/000-plan.md")" = "Status: done" ] \
  && ok "b, behind main, does not take 000 again: it syncs and sees the plan done" || bad "stale 000: claim $([ -d "$P/.agent/team/claims/000" ] && echo taken || echo refused), b's 000: $(head -1 "$P.b/tasks/000-plan.md")"
( cd "$P.b" && sed -i '1s/.*/Status: in-progress/' tasks/000-plan.md && git commit -qam "[driver] GOAL.md changed: planning task reopened" )
( cd "$P" && echo x > unrelated.txt && git add unrelated.txt && git commit -qm "unrelated work in main" )
as b 'claim_task tasks/000-plan.md' && [ "$(head -1 "$P.b/tasks/000-plan.md")" = "Status: in-progress" ] \
  && ok "a re-plan the driver reopened is still taken when the checkout is behind main" || bad "re-plan refused (behind)"
rm -rf "$P/.agent/team/claims/000"
as b 'claim_task tasks/000-plan.md' && ok "a re-plan reopened in an up-to-date checkout is taken" || bad "re-plan refused (up to date)"
rm -rf "$P/.agent/team/claims/000"

# --- the doorbell: once per set of open requests, not on every return to the wait ---
mkdir -p "$P.a/.agent/asks" "$T/bin"; printf 'status: open\nblocking: yes\ntask: tasks/101-leaf.md\n' > "$P.a/.agent/asks/001.md"
printf '#!/bin/sh\necho rang >> %s/rings\necho rang\n' "$T" > "$T/bin/ring-doorbell"; chmod +x "$T/bin/ring-doorbell"
wfh() { touch "$P.a/.agent/PAUSE"; as a "PATH=$T/bin:\$PATH ASK_REMIND_HOURS=6 WAITING=; wait_for_human" > /dev/null; rm -f "$P.a/.agent/PAUSE"; }
wfh; wfh
[ "$(wc -l < "$T/rings")" = 1 ] && ok "back to the wait for the same request: the doorbell rang once" || bad "rings for one request: $(wc -l < "$T/rings")"
printf 'status: open\nblocking: yes\ntask: tasks/105-side.md\n' > "$P.a/.agent/asks/002.md"; wfh
[ "$(wc -l < "$T/rings")" = 2 ] && ok "a new request: it rings again" || bad "rings after a new request: $(wc -l < "$T/rings")"
echo "001.md 002.md" > /dev/null; sed -i '2s/.*/0/' "$P.a/.agent/asks-rung"; wfh
[ "$(wc -l < "$T/rings")" = 3 ] && ok "after ASK_REMIND_HOURS: a reminder" || bad "no reminder: $(wc -l < "$T/rings")"
rm -rf "$P.a/.agent/asks" "$P.a/.agent/asks-rung"
exit $fail
