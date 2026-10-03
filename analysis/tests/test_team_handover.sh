#!/usr/bin/env bash
# test_team_handover.sh [DRIVER_DIR] : the smaller agent hands a task it cannot get through to a bigger agent that is
# waiting (frontpage 2026-10-03: b spent 10 sessions on 220 without ticking a box while a waited), and the goal check
# waits until every other task is finished (a 999 left open was taken while three tasks were still being built).
# Real git repo and worktrees; agents a (no limit) and b (TEAM_MAX_TOUCHES=8) are this shell's functions.
set -u
D=${1:-$HOME/bin}
T=$(mktemp -d); trap 'kill $apid 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home; mkdir -p "$HOME/.agent-kit/agents"
printf 'LLM_URL=x\n' > "$HOME/.agent-kit/agents/a.env"
printf 'LLM_URL=y\nTEAM_MAX_TOUCHES=8\n' > "$HOME/.agent-kit/agents/b.env"
P=$T/proj; mkdir -p "$P/tasks"; cd "$P"
git init -q -b main && git config user.email t@t && git config user.name t && echo ".agent/" > .gitignore
task() { printf 'Status: %s\n# %s\n\nDepends on: %s\nTouches: src/%s.py\n\n## Acceptance criteria\n- [ ] one\n- [ ] two\n' "$2" "$1" "$3" "${1%%-*}" > "tasks/$1.md"; }
task 220-cache open none; task 221-other open none; task 226-after open 220; task 230-old open none
task 999-goal-check open none; printf 'Status: done\n# 000\n' > tasks/000-plan.md
git add -A && git commit -q -m init
git worktree add -q "$P.a" -b agent/a && git worktree add -q "$P.b" -b agent/b
TM=$P/.agent/team; mkdir -p "$TM/claims" "$TM/loops"; : > "$TM/events.jsonl"
sleep 600 & apid=$!
echo $apid > "$TM/loops/a.pid"; echo $$ > "$TM/loops/b.pid"
claim() { mkdir -p "$TM/claims/$2"; echo "$1 $([ "$1" = a ] && echo $apid || echo $$) $(date +%s) $P.$1/tasks/$3" > "$TM/claims/$2/owner"; }
ev() { echo "{\"time\":\"t\",\"agent\":\"$1\",\"event\":\"$2\",\"task\":\"\",\"detail\":\"\"}" >> "$TM/events.jsonl"; }
as() {  # as <agent> <commands> : run in that agent's worktree with its identity and the driver's helpers
  ( cd "$P.$1" && export TEAM_DIR=$P AGENT_ID=$1 HOME; [ "$1" = b ] && export TEAM_MAX_TOUCHES=8
    eval "$(sed -n '/^status_of()/p; /^task_id()/p; /^commit_leftovers()/,/^}/p' "$D/agent-loop")"
    log() { echo "LOG $AGENT_ID $*" >> "$T/log"; }
    . "$D/agent-team-lib"; eval "$2" )
}

# the goal check waits for the rest of the queue
r=$(as a 'team_why_not tasks/999-goal-check.md')
[[ $r == "the goal check waits until every other task is finished (220 221 226 230)" ]] && ok "999 waits while tasks are open" || bad "999: [$r]"
( cd "$P" && for f in tasks/2*.md; do sed -i '1s/.*/Status: done/' "$f"; done )
[ -z "$(as a 'team_why_not tasks/999-goal-check.md')" ] && ok "999 free once every other task is done in main" || bad "999 still blocked"
( cd "$P" && git checkout -q -- tasks )

# b on 220: two sessions without a tick, a waiting -> nothing yet; the third -> hand-over
claim b 220 220-cache.md
( cd "$P.b" && echo "half-done" > half.py && git add half.py && git commit -q -m "220: half-done work" )
ev a claim; ev a wait_start
as b 'team_handover_check tasks/220-cache.md 0; team_handover_check tasks/220-cache.md 0'
[ -d "$TM/claims/220" ] && [ ! -e "$TM/handed/220" ] && ok "2 sessions without a tick: b keeps 220" || bad "handed over too early"
as b 'team_handover_check tasks/220-cache.md 0'
[ ! -d "$TM/claims/220" ] && ok "3rd session without a tick, a waiting: 220's claim released" || bad "claim still held"
read -r who _ st bk < "$TM/handed/220" 2>/dev/null
[ "$who" = b ] && [ "$st" = kept ] && [[ $bk == agent/b-handover-220-* ]] && ok "marker: handed by b, backup branch $bk" || bad "marker: $(cat "$TM/handed/220" 2>/dev/null)"
git -C "$P" show "$bk:half.py" > /dev/null 2>&1 && ok "b's half-done work is kept on the backup branch" || bad "work lost"
[ "$(git -C "$P.b" rev-parse HEAD)" = "$(git -C "$P" rev-parse main)" ] && [ ! -f "$P.b/half.py" ] && ok "b's branch is reset to main" || bad "b's branch not reset"
grep -q '"event":"handover"' "$TM/events.jsonl" && grep -q "handed over to agent a" "$T/log" && ok "event and log line" || bad "no event/log"
[[ $(as b 'team_why_not tasks/220-cache.md') == "handed over to a bigger agent" ]] && ok "b does not take 220 again" || bad "b: $(as b 'team_why_not tasks/220-cache.md')"
[ -z "$(as a 'team_why_not tasks/220-cache.md')" ] && ok "a can take 220" || bad "a: $(as a 'team_why_not tasks/220-cache.md')"

# a busy: no hand-over however long b takes
claim b 221 221-other.md; ev a claim
as b 'for i in 1 2 3 4; do team_handover_check tasks/221-other.md 0; done'
[ -d "$TM/claims/221" ] && [ ! -e "$TM/handed/221" ] && ok "a busy: b keeps 221 after 4 sessions" || bad "handed over while a worked"
# a tick resets the count: 0 0 tick 0 0 -> keeps; one more 0 -> hand-over
ev a wait_reason; rm -f "$P.b/.agent/progress-221"
as b 'team_handover_check tasks/221-other.md 0; team_handover_check tasks/221-other.md 0'
( cd "$P.b" && sed -i '0,/- \[ \]/s//- [x]/' tasks/221-other.md )
as b 'team_handover_check tasks/221-other.md 0; team_handover_check tasks/221-other.md 1; team_handover_check tasks/221-other.md 1'
[ -d "$TM/claims/221" ] && ok "a ticked box resets the count (0 0 tick 0 0: kept)" || bad "handed over despite the tick"
as b 'team_handover_check tasks/221-other.md 1'
[ ! -d "$TM/claims/221" ] && ok "then 3 in a row without a tick: handed over" || bad "not handed over after 3 more"

# the bigger agent never hands over; the plan and the goal check never move
claim a 226 226-after.md; ev b wait_start
as a 'for i in 1 2 3 4; do team_handover_check tasks/226-after.md 0; done'
[ -d "$TM/claims/226" ] && ok "agent a (no limit) never hands over" || bad "a handed over"
claim b 999 999-goal-check.md; ev a wait_start
as b 'for i in 1 2 3 4; do team_handover_check tasks/999-goal-check.md 0; done'
[ -d "$TM/claims/999" ] && ok "the goal check is never handed over" || bad "999 handed over"

# a hand-over marked while b's loop was stopped (pending) is finished at b's next start
( cd "$P.b" && echo "old" > old.py && git add old.py && git commit -q -m "230: old work" )
mkdir -p "$TM/handed"; echo "b $(date +%s) pending" > "$TM/handed/230"
as b 'team_handover_finish'
read -r who _ st bk < "$TM/handed/230"
[ "$st" = kept ] && git -C "$P" show "$bk:old.py" > /dev/null 2>&1 && [ ! -f "$P.b/old.py" ] \
  && ok "pending hand-over finished at start: backup $bk, branch reset" || bad "pending: $(cat "$TM/handed/230")"
as b 'team_handover_finish'; [ "$(git -C "$P" branch --list 'agent/b-handover-230-*' | wc -l)" = 1 ] && ok "finishing twice makes one backup" || bad "two backups"

# a finished task's open requests are withdrawn (answering one would reopen the task); others stay open
( cd "$P.b" && mkdir -p .agent/asks
  printf '# Request 001\nstatus: open\nblocking: no\ntask: tasks/220-cache.md\n' > .agent/asks/001.md
  printf '# Request 002\nstatus: open\nblocking: no\ntask: tasks/221-other.md\n' > .agent/asks/002.md
  log() { :; }; eval "$(sed -n '/^ask_field()/p; /^withdraw_asks_of()/,/^}/p; /^asks_with()/,/^}/p' "$D/agent-loop")"
  withdraw_asks_of tasks/220-cache.md )
grep -q '^status: withdrawn' "$P.b/.agent/asks/001.md" && grep -q '^## Withdrawn' "$P.b/.agent/asks/001.md" \
  && grep -q '^status: open' "$P.b/.agent/asks/002.md" && ok "accepted task: its open request withdrawn, another task's stays open" || bad "withdraw: $(head -2 "$P.b/.agent/asks/001.md" | tail -1)"
exit $fail
