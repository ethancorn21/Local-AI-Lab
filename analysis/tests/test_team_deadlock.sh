#!/usr/bin/env bash
# test_team_deadlock.sh [DRIVER_DIR] : deadlocks in team mode, the three rules against them, function by function.
# frontpage 2026-10-07: 256 (agent c, the v3 row template) waited for 257 (agent a, its e2e test) and 257 for 256 - a
# merge of either alone left main red - and each loop waited for the other's task to reach main; 257 was also set
# blocked with no request to the human filed. All three agents sat idle for four hours. Checked:
#   - the old picture: neither holder can take its own task (the deadlock reproduced)
#   - Cycles: team-takeover cycles finds 256/257 and gives them to c (more of the cycle's files: less work moves); c
#     asks for 257, a gives it with its work, c applies it; for c both are workable, for a nothing is; the prompt notes
#   - dead statuses: blocked with no open (or answered) request anywhere in the team, or an unknown status, counts as
#     in-progress; revive_task sets it in-progress (a task file without a Status line gets one), with the prompt's note
#   - finished claims are released (all their tasks done here and in main), unfinished ones kept
#   - Stall: the lead notices every running agent waiting TEAM_STALL_MIN with work left and no request open; tells the
#     human once; not while a request is open, an agent works, or a loop's state is unknown; each agent then forces its
#     own most important claim, one per stall, each task at most TEAM_STALL_FORCES times
# Real git repo and worktrees; agents are this shell's functions run with different AGENT_IDs. Run on the VM.
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/driver" && pwd)}
T=$(mktemp -d); trap 'kill $apid $cpid 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home PATH="$T/bin:$D:$PATH"; mkdir -p "$HOME/.agent-kit/agents" "$T/bin"
printf 'cat >> %s/doorbell.txt; echo "---" >> %s/doorbell.txt; echo sent\n' "$T" "$T" > "$T/bin/ring-doorbell"; chmod +x "$T/bin/ring-doorbell"
printf 'LLM_URL=x\nTEAM_SPEED=4\n' > "$HOME/.agent-kit/agents/a.env"
printf 'LLM_URL=y\nTEAM_SPEED=4\n' > "$HOME/.agent-kit/agents/c.env"
P=$T/proj; mkdir -p "$P/tasks" "$P/src" "$P/e2e"; cd "$P"
git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
task() { printf 'Status: %s\n# %s\n\nDepends on: %s\nTouches: %s\n\n## Acceptance criteria\n- [ ] works\n' "$2" "$1" "$3" "$4" > "tasks/$1.md"; }
printf 'Status: done\n# 000\n' > tasks/000-plan.md
task 256-template in-progress 257 "src/rows.html, src/feed.py, src/pages.py"
task 257-e2e open 256 "e2e/rows.spec.mjs, src/test_e2e.py"
task 233-batches open "256, 257" "src/batches.py"
task 802-perf open 256 "src/perf.py"
task 240-old done none "src/old.py"
for f in src/rows.html src/feed.py src/pages.py e2e/rows.spec.mjs src/test_e2e.py; do echo base > "$f"; done
git add -A && git commit -q -m init
git worktree add -q "$P.a" -b agent/a && git worktree add -q "$P.c" -b agent/c
TM=$P/.agent/team; mkdir -p "$TM/claims" "$TM/loops" "$TM/agents" "$P.a/.agent" "$P.c/.agent"
echo "$P.a" > "$TM/agents/a"; echo "$P.c" > "$TM/agents/c"
sleep 900 & apid=$!; sleep 900 & cpid=$!
echo $apid > "$TM/loops/a.pid"; echo $cpid > "$TM/loops/c.pid"
claim() { mkdir -p "$TM/claims/$2"; echo "$1 $([ "$1" = a ] && echo $apid || echo $cpid) $(date +%s) $P.$1/tasks/$3" > "$TM/claims/$2/owner"; }
owner() { cut -d' ' -f1 "$TM/claims/$1/owner" 2>/dev/null; }
fns() {  # the definition of one function of agent-loop (one line or a block)
  awk -v n="$1" '!p && $0 ~ "^"n"\\(\\) *\\{" { print; if ($0 ~ /; }[[:space:]]*(#.*)?$/) exit; p = 1; next } p { print } p && /^}/ { exit }' "$D/agent-loop"
}
LOOPFNS=$(for f in status_of task_id handover_of pending_subtasks workable next_task commit_leftovers ask_field asks_with dead_status revive_task; do fns $f; done)
as() {  # as <agent> <commands> : in that agent's checkout, with its identity and the driver's helpers
  ( cd "$P.$1" && export TEAM_DIR=$P AGENT_ID=$1 HOME TEAM_SPEED=4
    eval "$LOOPFNS"
    log() { echo "LOG $AGENT_ID $*" >> "$T/log"; }
    . "$D/agent-team-lib"; eval "$2" )
}
ev() { local n; n=$(grep -c "\"event\":\"$1\"" "$TM/events.jsonl" 2>/dev/null); echo "${n:-0}"; }
commit_in() { git -C "$P.$1" add -A && git -C "$P.$1" commit -q -m "$2"; }

claim a 257 257-e2e.md; claim a 233 233-batches.md; claim c 256 256-template.md; claim c 802 802-perf.md
cd "$P.a"; echo "spec v3" > e2e/rows.spec.mjs; commit_in a "257: spec edit applied"
sed -i '1s/.*/Status: blocked/' tasks/257-e2e.md; printf '\n## Hand-over\nblocked on the 256<->257 merge order; ask the human next session\n' >> tasks/257-e2e.md
commit_in a "257: blocked on the merge-order cycle"
cd "$P.c"; echo "rows v3" > src/rows.html; commit_in c "256: template v3"
cd "$P"

# --- the deadlock as it was: neither holder can take its own task ---
[ -z "$(as a next_task)" ] && [ -z "$(as c next_task)" ] && ok "before the cycle is known: a and c have nothing to take (the 2026-10-07 deadlock)" || bad "a: $(as a next_task), c: $(as c next_task)"

# --- Cycles ---
facts=$(as a team_cycle_facts)
grep -qP '^256\t257 \tc\t1\t4\t3$' <<<"$facts" && grep -qP '^257\t256 \ta\t1\t4\t2$' <<<"$facts" && ok "facts: 256 (c, 3 files) waits for 257, 257 (a, as a's checkout has it) for 256" || bad "facts: $facts"
cyc=$(as a 'team_cycles_sync; printf "%s" "$TEAM_CYCLES"')
[ "$cyc" = "$(printf 'c\t256 257\t257')" ] && ok "cycle 256 257 goes to c (equal speed, more of the cycle's files); 257 is to move" || bad "cycles: $cyc"
[ ! -d "$TM/takeover/257" ] && [ "$(ev cycle)" = 1 ] && ok "a asks for nothing; the cycle is logged once" || bad "a asked, or cycle events: $(ev cycle)"
as a team_cycles_sync; [ "$(ev cycle)" = 1 ] && ok "seen again: not logged again" || bad "cycle logged $(ev cycle) times"
as c team_cycles_sync
[ "$(cut -d' ' -f1 "$TM/takeover/257/asker" 2>/dev/null)" = c ] && [ "$(cat "$TM/takeover/257/why" 2>/dev/null)" = "cycle 256 257" ] \
  && ok "c asks a for 257 (takeover/257: asker c, why 'cycle 256 257')" || bad "no cycle ask: $(ls "$TM/takeover" 2>/dev/null)"
as a team_takeover_answer
[ "$(owner 257)" = c ] && ok "a gives 257 to c between sessions" || bad "257 held by $(owner 257)"
[ "$(sed -n 3p "$TM/carry/257" 2>/dev/null)" = "cycle 256 257" ] && ok "the carry says why" || bad "carry: $(cat "$TM/carry/257" 2>/dev/null)"
[ "$(git -C "$P.a" show HEAD:e2e/rows.spec.mjs)" = base ] && ok "a's branch: 257's file put back as main has it" || bad "a's branch still has 257's work"
grep -q 'tasks 256 257 wait on each other' "$T/log" && ok "a's log says why it gave 257" || bad "log: $(grep 'gave 257' "$T/log")"
as c team_takeover_receive
[ "$(git -C "$P.c" show HEAD:e2e/rows.spec.mjs)" = "spec v3" ] && [ "$(git -C "$P.c" show HEAD:src/rows.html)" = "rows v3" ] \
  && ok "c's branch: 257's work carried in next to 256's" || bad "c's branch: spec '$(git -C "$P.c" show HEAD:e2e/rows.spec.mjs)'"
[ "$(as c 'team_cycles_sync; printf "%s" "$TEAM_CYCLES"')" = "$(printf 'c\t256 257\t')" ] && ok "after the move: c holds the whole cycle, nothing left to move" || bad "cycles now: $(as c 'team_cycles_sync; echo "$TEAM_CYCLES"')"
nt=$(as c 'team_cycles_sync; next_task')
case $nt in tasks/256-template.md|tasks/257-e2e.md) ok "c takes a task of the cycle ($nt)";; *) bad "c's next task: '$nt'";; esac
as c 'team_cycles_sync; workable tasks/256-template.md any && workable tasks/257-e2e.md any' && ok "for c both cycle tasks are workable (257 blocked with no request: dead status)" || bad "not both workable for c"
[ -z "$(as a 'team_cycles_sync; next_task')" ] && ok "a still has nothing: 233 waits for 256 and 257 (c's)" || bad "a took $(as a 'team_cycles_sync; next_task')"
as c 'team_cycles_sync; team_cycle_note tasks/256-template.md' | grep -q 'CYCLE: tasks 256, 257 wait on each other' && ok "c's prompt: the CYCLE note" || bad "no cycle note"
as c 'team_takeover_note tasks/257-e2e.md' | grep -q 'part of a cycle' && ok "c's prompt on 257: TAKEN OVER, as part of a cycle" || bad "takeover note: $(as c 'team_takeover_note tasks/257-e2e.md')"
note=$(as c 'revive_task tasks/257-e2e.md')
[ "$(head -1 "$P.c/tasks/257-e2e.md")" = "Status: in-progress" ] && grep -q 'UNBLOCKED' <<<"$note" && git -C "$P.c" log -1 --format=%s | grep -q "status 'blocked'" \
  && ok "revive: 257 set in-progress in c's checkout, committed, the prompt says why" || bad "revive: $(head -1 "$P.c/tasks/257-e2e.md") / $note"
[ -z "$(as c 'revive_task tasks/256-template.md')" ] && ok "revive leaves an in-progress task alone" || bad "revived 256"
[ "$(as c 'TEAM_CYCLE_FIX=0 team_cycles_sync; printf "%s" "$TEAM_CYCLES"')" = "" ] && ok "TEAM_CYCLE_FIX=0: no cycles" || bad "TEAM_CYCLE_FIX=0 still finds cycles"
printf 'Status: open\n# 300\nDepends on: 300\nTouches: src/x.py\n' > "$P/tasks/300-self.md"; git -C "$P" add -A; git -C "$P" commit -q -m 300
grep -qP '^-\t300\t$' <<<"$(as a 'team_cycles_sync; printf "%s" "$TEAM_CYCLES"')" && as a 'team_sync >/dev/null; team_cycles_sync; workable tasks/300-self.md any' \
  && ok "a task that depends on itself: a cycle nobody holds, workable" || bad "self-dependency: $(as a 'team_cycles_sync; echo "$TEAM_CYCLES"')"
git -C "$P" rm -q tasks/300-self.md; git -C "$P" commit -q -m "drop 300"; as a 'team_sync >/dev/null'

# --- dead statuses ---
cd "$P.a"; mkdir -p .agent/asks "$P.c/.agent/asks"
printf 'Status: blocked\n# 270\nDepends on: none\nTouches: src/q.py\n' > tasks/270-q.md
printf 'Status: waiting\n# 271\nDepends on: none\nTouches: src/r.py\n' > tasks/271-r.md
printf '# 272: no status line\nDepends on: none\nTouches: src/s.py\n' > tasks/272-s.md
commit_in a "dead status cases"
ask() { printf 'status: %s\nblocking: yes\ntask: tasks/270-q.md\n' "$2" > "$1/.agent/asks/001.md"; }
dead() { as a "dead_status $1"; }
dead tasks/270-q.md && ok "blocked with no request to the human: dead" || bad "270 blocked, no request: should be dead"
ask "$P.a" open; dead tasks/270-q.md && bad "270 dead with an open request" || ok "blocked with an open request: waiting, not dead"
ask "$P.a" answered; dead tasks/270-q.md && bad "270 dead with an answered request" || ok "blocked with an answered request: not dead (answered_first resumes it)"
ask "$P.a" closed; dead tasks/270-q.md && ok "blocked, its request closed: dead" || bad "270 not dead with a closed request"
rm "$P.a/.agent/asks/001.md"; ask "$P.c" open; dead tasks/270-q.md && bad "270 dead with an open request in c's checkout" || ok "blocked with an open request in another agent's checkout: not dead"
printf 'status: closed\nblocking: yes\ntask: tasks/999-x.md\n' > "$P.c/.agent/asks/001.md"; printf 'tasks/270-q.md\n' > "$P.a/.agent/asks/002.waiters"
printf 'status: open\nblocking: yes\ntask: tasks/233-batches.md\n' > "$P.a/.agent/asks/002.md"
dead tasks/270-q.md && bad "270 dead while a waiter of an open request" || ok "blocked as a waiter of another task's open request: not dead"
rm -f "$P.a/.agent/asks/"* "$P.c/.agent/asks/"*
dead tasks/271-r.md && ok "an unknown status ('waiting') is dead" || bad "271 not dead"
( cd "$P.a" && eval "$LOOPFNS"; dead_status tasks/270-q.md ) && ok "solo mode (no TEAM_DIR): dead_status works on its own checkout" || bad "solo dead_status"
as a 'revive_task tasks/272-s.md > /dev/null'
[ "$(head -2 "$P.a/tasks/272-s.md" | tr '\n' '|')" = "Status: in-progress|# 272: no status line|" ] && ok "revive: a task file without a Status line gets one, its title kept" || bad "272: $(head -2 "$P.a/tasks/272-s.md")"
git -C "$P.a" rm -q tasks/270-q.md tasks/271-r.md tasks/272-s.md; commit_in a "drop dead status cases"

# --- finished claims ---
claim a 240 240-old.md
as a team_release_finished; [ ! -d "$TM/claims/240" ] && [ -d "$TM/claims/233" ] && ok "a's claim on 240 (done here and in main) released; 233 kept" || bad "claims: $(ls "$TM/claims")"
claim c 803 803-x.md; printf 'Status: done\n# 803\n' > "$P.c/tasks/803-x.md"; printf 'Status: open\n# 803a\n' > "$P.c/tasks/803a-y.md"
as c team_release_finished; [ -d "$TM/claims/803" ] && ok "a claim with an unfinished subtask is kept" || bad "803 released"
rm -rf "$TM/claims/803"; rm -f "$P.c/tasks/803-x.md" "$P.c/tasks/803a-y.md"

# --- Stall ---
now=$(date +%s); state() { echo "$2 $3" > "$TM/loops/$1.state"; }
state a wait $((now - 1200)); state c wait $((now - 1000))
as c team_stall_check; [ ! -f "$TM/stall" ] && ok "only the lead (a) judges a stall" || bad "c wrote a stall"
as a team_stall_check
[ "$(cut -d' ' -f1 "$TM/stall" 2>/dev/null)" = $((now - 1000)) ] && [ "$(ev team_stall)" = 1 ] && grep -q 'every agent has had nothing to build for 16 min' "$T/doorbell.txt" 2>/dev/null \
  && ok "a: stall since the last agent began waiting (16 min), logged, the human told" || bad "stall: $(cat "$TM/stall" 2>/dev/null) events $(ev team_stall) doorbell: $(head -3 "$T/doorbell.txt" 2>/dev/null)"
as a team_stall_check; [ "$(ev team_stall)" = 1 ] && [ "$(grep -c '^---$' "$T/doorbell.txt")" = 1 ] && ok "the same stall: not told again" || bad "told twice"
as a 'TEAM_STALL_MIN=30 team_stall_check'; [ ! -f "$TM/stall" ] && ok "TEAM_STALL_MIN=30: 16 min is no stall" || bad "stall at 16 of 30 min"
as a team_stall_check; [ -f "$TM/stall" ] || bad "stall not back"
state c work $((now - 50)); as a team_stall_check; [ ! -f "$TM/stall" ] && ok "an agent in a session: no stall" || bad "stall while c works"
state c wait $((now - 1000)); printf 'status: open\nblocking: yes\ntask: tasks/802-perf.md\n' > "$P.c/.agent/asks/001.md"
as a team_stall_check; [ ! -f "$TM/stall" ] && ok "a request open (in c's checkout): waiting for the human, no stall" || bad "stall with a request open"
rm -f "$P.c/.agent/asks/001.md"; rm -f "$TM/loops/c.state"
as a team_stall_check; [ ! -f "$TM/stall" ] && ok "a loop without a state (started before this rule): no verdict" || bad "stall with c's state unknown"
state c wait $((now - 1000)); as a team_stall_check; [ -f "$TM/stall" ] || bad "stall not back (2)"
[ "$(as c 'team_cycles_sync; team_idle_decision')" = "stall tasks/$(basename "$(as c 'team_cycles_sync; team_force_pick')")" ] && ok "c's idle decision during a stall: start its own task anyway" || bad "decision: $(as c 'team_cycles_sync; team_idle_decision')"
f=$(as c 'team_cycles_sync; team_force_pick')
case $f in tasks/256-template.md|tasks/257-e2e.md|tasks/802-perf.md) ok "c forces its own claim ($f)";; *) bad "c force pick: '$f'";; esac
n=$(as c "team_forced $f"); grep -q 'FORCED START' <<<"$n" && [ "$(ev stall_force)" = 1 ] && ok "forced: logged, the prompt's FORCED START note" || bad "forced note: $n"
[ -z "$(as c 'team_cycles_sync; team_force_pick')" ] && ok "one forced start per agent per stall" || bad "c forces again: $(as c team_force_pick)"
fa=$(as a 'team_cycles_sync; team_force_pick'); [ "$fa" = tasks/233-batches.md ] && ok "a forces its own 233 (waits for c's tasks)" || bad "a force pick: '$fa'"
k=$(basename "$f" | cut -d- -f1)
echo "$((now - 900)) $now" > "$TM/stall"; [ "$(as c 'team_cycles_sync; team_force_pick')" = "$f" ] && as c "team_forced $f > /dev/null" && ok "a new stall: c may force $k a second time" || bad "second stall: $(as c team_force_pick)"
echo "$((now - 800)) $now" > "$TM/stall"; g=$(as c 'team_cycles_sync; team_force_pick'); [ -n "$g" ] && [ "$g" != "$f" ] && ok "third stall: $k is spent (TEAM_STALL_FORCES 2), c forces another claim ($g)" || bad "third stall pick: '$g'"
rm -f "$TM/stall"; [ "$(as c 'team_cycles_sync; team_idle_decision')" = wait ] && ok "no stall: c waits" || bad "decision without stall: $(as c team_idle_decision)"

echo; [ "$fail" = 0 ] && echo "ALL OK" || echo "SOME CHECKS FAILED (log: $(wc -l < "$T/log" 2>/dev/null) lines)"
exit "$fail"
