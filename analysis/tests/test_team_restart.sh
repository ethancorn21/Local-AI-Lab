#!/usr/bin/env bash
# test_team_restart.sh [AGENT_TEAM] : `agent-team restart` restarts only the agents that were running. An agent that
# was stopped (on purpose: frontpage 2026-10-03, b stopped with unmerged half-work on its branch) stays stopped.
# agent-stop and agent-start are stubs that log their calls; a running loop is a held loop.lock, as in agent-loop.
set -u
TEAMCMD=${1:-$HOME/bin/agent-team}
T=$(mktemp -d); trap 'kill $holder 2>/dev/null; rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
export HOME=$T/home; mkdir -p "$HOME/bin" "$HOME/projects/p/.agent/team/agents" "$HOME/projects/p/.agent/team/loops"
touch "$HOME/projects/p/.agent/team-main"
for id in a b; do mkdir -p "$HOME/projects/p.$id/.agent"; echo "$HOME/projects/p.$id" > "$HOME/projects/p/.agent/team/agents/$id"; done
printf '#!/bin/sh\necho "stop $1" >> %s/calls\n' "$T" > "$HOME/bin/agent-stop"
printf '#!/bin/sh\necho "start $1" >> %s/calls\necho started\n' "$T" > "$HOME/bin/agent-start"
chmod +x "$HOME/bin/"*
# agent a's loop runs (holds its lock) and ends its session 3 s after it is told to stop; b is stopped
flock "$HOME/projects/p.a/.agent/loop.lock" sleep 3 & holder=$!
sleep 0.5
out=$(timeout 60 bash "$TEAMCMD" restart p 2>&1); rc=$?
[ $rc -eq 0 ] && ok "restart finished (rc 0)" || bad "restart rc $rc: $out"
grep -qx "stop $HOME/projects/p.a" "$T/calls" && grep -qx "start $HOME/projects/p.a" "$T/calls" \
  && ok "running agent a: stopped, then started" || bad "agent a calls: $(cat "$T/calls" 2>/dev/null | tr '\n' ';')"
! grep -q "p.b" "$T/calls" && ok "stopped agent b: never started" || bad "agent b touched: $(grep p.b "$T/calls" | tr '\n' ';')"
echo "$out" | grep -q "agent b: stopped, left stopped" && ok "says b was left stopped" || bad "no note about b: $out"
[ "$(grep -n "start $HOME/projects/p.a" "$T/calls" | cut -d: -f1)" -gt "$(grep -n "stop $HOME/projects/p.a" "$T/calls" | cut -d: -f1)" ] \
  && ok "a started only after it was stopped" || bad "order"
: > "$T/calls"; wait $holder 2>/dev/null
out=$(timeout 20 bash "$TEAMCMD" restart p 2>&1); rc=$?
[ $rc -ne 0 ] && [ ! -s "$T/calls" ] && echo "$out" | grep -q "no agent is running" \
  && ok "nothing running: refuses and starts nobody" || bad "nothing running: rc $rc, calls $(cat "$T/calls" | tr '\n' ';')"
exit $fail
