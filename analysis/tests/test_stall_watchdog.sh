#!/usr/bin/env bash
# test_stall_watchdog.sh DRIVER : the driver's stall watchdog, without a model (about 3 minutes).
# 1 hung: a session with an unfinished pytest call and a detached grandchild in its own session -> both killed at
#   ~STALL_KILL, .agent/stalled written, the stall note names the command.
# 2 busy: a session that keeps writing output -> left alone.
# 3 long: the unfinished call asked for timeout 300 -> still alive after 120 s although STALL_KILL is 60.
set -u
DRIVER=${1:-$HOME/bin/agent-loop}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
sed -n '/^# --- stall watchdog ---/,/^reopen() {/p' "$DRIVER" | sed '$d' > "$T/fns.sh"
export STALL_KILL=60
fail=0
run_case() {  # run_case <name> <session body>
  local d=$T/$1; mkdir -p "$d/.agent"; cd "$d" || exit 1
  printf 'Status: in-progress\n# 999: test task\n' > task.md
  bash -c "$2" > s.jsonl 2>/dev/null & local spid=$!
  echo "$spid" > spid
  (. "$T/fns.sh"; stall_watch "$spid" s.jsonl) & echo $! > wpid
}
start='{"type":"tool_execution_start","toolCallId":"a","toolName":"bash","args":{"command":"python3 -m pytest -q"%s}}'
run_case hung "echo '$(printf "$start" "")'; setsid sleep 1001 & sleep 1000"
run_case busy "echo '$(printf "$start" "")'; for i in \$(seq 1 16); do echo '{\"type\":\"tool_execution_update\"}'; sleep 10; done"
run_case long "echo '$(printf "$start" ',"timeout":300')'; sleep 1002"
sleep 125
alive() { kill -0 "$(cat "$T/$1/spid")" 2>/dev/null; }
if alive hung || [ ! -f "$T/hung/.agent/stalled" ] || pgrep -f 'sleep 1001' > /dev/null; then
  echo "FAIL hung: session or detached child still running, or no .agent/stalled"; fail=1
else
  (cd "$T/hung" && . "$T/fns.sh" && stall_note task.md s.jsonl "$(cat .agent/stalled)")
  if grep -q "python3 -m pytest -q" "$T/hung/task.md" && grep -q "^## Hand-over" "$T/hung/task.md"; then
    echo "ok   hung: killed after $(cat "$T/hung/.agent/stalled") s idle, detached child gone, note names the command"
  else echo "FAIL hung: stall note missing or wrong"; cat "$T/hung/task.md"; fail=1; fi
fi
if alive busy && [ ! -f "$T/busy/.agent/stalled" ]; then echo "ok   busy: left alone"; else echo "FAIL busy: was stopped"; fail=1; fi
if alive long && [ ! -f "$T/long/.agent/stalled" ]; then echo "ok   long: own timeout 300 s respected past STALL_KILL"
else echo "FAIL long: stopped before its own timeout"; fail=1; fi
for c in busy long; do kill "$(cat "$T/$c/spid")" "$(cat "$T/$c/wpid")" 2>/dev/null; done
pkill -f 'sleep 100[0-2]' 2>/dev/null
exit $fail
