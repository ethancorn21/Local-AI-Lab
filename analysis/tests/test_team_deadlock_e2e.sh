#!/usr/bin/env bash
# test_team_deadlock_e2e.sh [DRIVER_DIR] : frontpage's 2026-10-07 deadlock end to end, two stub agents, real loops
# (about 8-12 minutes). 001 and 002 each find, in their first session, that they need the other (stubpi-team's
# Stub-needs: the agent adds it to its Depends on line and ends the session), while agents a and b hold one each; 003
# waits for both. Run on the VM.
#   cycle:   the cycle rule gathers 001 and 002 on one agent, which builds both; everything finishes
#   stall:   with the cycle rule off (TEAM_CYCLE_FIX=0), the stall net (TEAM_STALL_MIN=1) notices both agents waiting,
#            tells the human, and starts each agent's own task anyway; everything finishes
#   old:     with both off: the deadlock as it was - two minutes after the cycle formed, nothing has moved
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}
T=$(mktemp -d); trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT
H=$T/home; PR=$H/projects; mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit/agents" "$PR"
for f in agent-loop agent-team-lib agent-team task-audit codemap-gen decisions-archive plan-schedule pitfalls-sync team-takeover; do [ -f "$DRIVER/$f" ] && cp "$DRIVER/$f" "$H/bin/"; done
for f in agent-start agent-stop; do sudo cat "/home/agent/bin/$f" > "$H/bin/$f"; done
cp "$HERE/stubpi-team" "$H/.npm-global/bin/pi"
printf 'cat >> %s/doorbell.txt; echo --- >> %s/doorbell.txt; echo sent\n' "$T" "$T" > "$H/bin/ring-doorbell"
chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
sudo cp -r /home/agent/.agent-kit/template "$H/.agent-kit/" && sudo chown -R "$(id -u)" "$H/.agent-kit/template"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
: > "$H/.agent-kit/agents/a.env"; : > "$H/.agent-kit/agents/b.env"
FX=$T/fixtures; mkdir -p "$FX"
task() { printf 'Status: open\n# %s\n\nDepends on: %s\nTouches: %s\n%s\n## Acceptance criteria\n- [ ] done\n' "${1%.md}" "$2" "$3" "${4:-}" > "$FX/$1"; }
task 001-left.md none "src/left/" $'Stub-needs: 002\nStub-sleep: 6'
task 002-right.md none "src/right/" $'Stub-needs: 001\nStub-sleep: 6'
task 003-top.md "001, 002" "src/top/"
export HOME=$H STUB_FIXTURES=$FX STUB_SLEEP=3 TEAM_WAIT_S=3 TEST_CMD=true NO_PROGRESS_LIMIT=20
export PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin"
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
export LLM_URL=http://127.0.0.1:$MS
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
ev() { local n; n=$(grep -c "\"event\":\"$2\"" "$PR/$1/.agent/team/events.jsonl" 2>/dev/null); echo "${n:-0}"; }
running() { pgrep -u "$(id -u)" -f "agent-loop $PR/$1" > /dev/null; }
cycle_formed() {  # both agents' first sessions ended with the other task added to Depends on
  local n=0 w
  for w in "$PR/$1.a" "$PR/$1.b"; do git -C "$w" log --format=%s 2>/dev/null | grep -q 'added it to Depends on' && n=$((n + 1)); done
  [ "$n" = 2 ]
}
start() { mkdir -p "$PR/$1"; echo "# Test goal" > "$PR/$1/GOAL.md"; agent-team init "$1" a b > /dev/null && agent-team start "$1" > /dev/null; }
finishes() {  # finishes <name> : wait (max ~8 min) for the goal check's STOP and both loops gone, then check main
  local i nd
  for i in $(seq 1 100); do sleep 5; [ -f "$PR/$1/.agent/team/STOP" ] && ! running "$1" && break; done
  agent-team status "$1" | sed 's/^/     /' | head -20
  [ -f "$PR/$1/.agent/team/STOP" ] && ! running "$1" && ok "$1: finished (goal check ran, both loops stopped)" || { bad "$1: did not finish"; agent-team stop "$1" --now > /dev/null; }
  nd=$(for f in "$PR/$1"/tasks/[0-9]*.md; do head -1 "$f"; done | grep -vc 'Status: done')
  [ "$nd" -eq 0 ] && ok "$1: every task done in main" || bad "$1: $nd task(s) not done in main"
  [ -e "$PR/$1/src/left" ] && [ -e "$PR/$1/src/right" ] && [ -e "$PR/$1/src/top" ] && ok "$1: all three tasks' work in main" || bad "$1: main lacks work: $(ls "$PR/$1/src" 2>/dev/null)"
}
held_apart() {  # 001 and 002 were claimed by different agents (the deadlock's precondition)
  local a1 a2
  a1=$(grep '"event":"claim"' "$PR/$1/.agent/team/events.jsonl" | grep '/001-' | head -1 | jq -r .agent)
  a2=$(grep '"event":"claim"' "$PR/$1/.agent/team/events.jsonl" | grep '/002-' | head -1 | jq -r .agent)
  [ -n "$a1" ] && [ -n "$a2" ] && [ "$a1" != "$a2" ] && ok "$1: 001 and 002 first held by different agents ($a1, $a2)" || bad "$1: 001 by '$a1', 002 by '$a2' (no cross-held cycle: the scenario did not happen)"
}

# --- cycle: the cycle rule ---
start dc; finishes dc; held_apart dc
[ "$(ev dc cycle)" -ge 1 ] && ok "dc: the cycle was found ($(grep '"event":"cycle"' "$PR/dc/.agent/team/events.jsonl" | head -1 | jq -r .detail))" || bad "dc: no cycle event"
grep '"event":"takeover"' "$PR/dc/.agent/team/events.jsonl" | grep -q 'cycle 001 002' && ok "dc: one agent gave its task to the other (cycle 001 002)" || bad "dc: no cycle takeover"
[ "$(ev dc team_stall)" = 0 ] && ok "dc: no stall (the cycle rule acted first)" || bad "dc: a stall happened too"

# --- stall: the net alone ---
export TEAM_CYCLE_FIX=0 TEAM_STALL_MIN=1
start ds; finishes ds; held_apart ds
[ "$(ev ds cycle)" = 0 ] && ok "ds: cycle rule off" || bad "ds: the cycle rule acted"
[ "$(ev ds team_stall)" -ge 1 ] && [ "$(ev ds stall_force)" -ge 2 ] && ok "ds: stall noticed, both agents started their task anyway ($(ev ds stall_force) forced starts)" || bad "ds: stalls $(ev ds team_stall), forced $(ev ds stall_force)"
grep -q 'nothing to build for' "$T/doorbell.txt" 2>/dev/null && ok "ds: the human was told (doorbell)" || bad "ds: no doorbell"
grep -h 'FORCED START' "$PR/ds.a/.agent/stub-prompts.txt" "$PR/ds.b/.agent/stub-prompts.txt" > /dev/null 2>&1 && ok "ds: the forced session's prompt says so" || bad "ds: no FORCED START in prompts"

# --- old: both off, the deadlock as it was ---
export TEAM_CYCLE_FIX=0 TEAM_STALL_MIN=0
start do
for _ in $(seq 1 60); do sleep 5; cycle_formed do && break; done
cycle_formed do && ok "do: the cycle formed (001 needs 002, 002 needs 001, held by a and b)" || bad "do: the cycle never formed"
sleep 120
! grep '"event":"merge"' "$PR/do/.agent/team/events.jsonl" | grep -qE '/00[12]-' && running do \
  && ok "do: two minutes later nothing has moved, both loops still waiting (the deadlock reproduced)" || bad "do: something moved: $(grep -c merge "$PR/do/.agent/team/events.jsonl")"
agent-team stop do --now > /dev/null 2>&1; sleep 3; running do && pkill -u "$(id -u)" -f "agent-loop $PR/do"

echo; [ "$fail" = 0 ] && echo "ALL OK" || echo "SOME CHECKS FAILED"
exit "$fail"
