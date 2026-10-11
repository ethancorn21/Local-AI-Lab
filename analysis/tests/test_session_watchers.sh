#!/usr/bin/env bash
# test_session_watchers.sh [DRIVER_DIR] : a prep or carve session's background watcher ends with its session
# (agent-loop, run_session). ancient-works 2026-10-10: run_session kept its stall watcher's pid in the caller's
# `wpid`, so prep_iteration and carve_iteration killed the stall watcher twice and never their own. Agent a's
# watchers from nine prep sessions (201-210) kept running: each beat the agent's heartbeat and answered takeover
# requests every 20 s, and when the re-plan (000) became free for a they all wrote .agent/wrapup-now at once.
# Checked here, with the real prep_iteration, carve_iteration and run_session and a stub agent: after each session,
# its watcher (a stand-in that logs while it lives) is gone.
set -u
D=${1:-$(cd "$(dirname "$0")/../../harness/driver" && pwd)}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
fns() {  # the definition of one function of agent-loop (one line or a block)
  awk -v n="$1" '!p && $0 ~ "^"n"\\(\\) *\\{" { print; if ($0 ~ /; }[[:space:]]*(#.*)?$/) exit; p = 1; next } p { print } p && /^}/ { exit }' "$D/agent-loop"
}
mkdir -p "$T/bin" "$T/p/.agent/sessions" "$T/p/tasks/prep"
printf '#!/bin/sh\nsleep 1\necho "{}"\n' > "$T/bin/pi"; chmod +x "$T/bin/pi"   # the stub agent: a one-second session
cd "$T/p" && git init -q && git config user.email t@t && git config user.name t && git commit -q --allow-empty -m init
alive() { local n0 n1; n0=$(wc -l < "$T/$1.log"); sleep 3; n1=$(wc -l < "$T/$1.log"); [ "$n1" -gt "$n0" ]; }   # its watcher still logs
(
  export PATH="$T/bin:$PATH"; VIEWER=json AGENT_CMD=pi ITER_TIMEOUT=30 THINKING=low LEDGER=$T/ledger i=1 P=$T/p
  eval "$(for f in run_session prep_iteration carve_iteration; do fns $f; done)"
  log() { :; }; tev() { :; }; harness_stamp() { HARNESS_ID=x HARNESS='{}'; }; codemap_sync() { :; }; pitfalls_sync() { :; }
  archive_sync() { :; }; prep_file() { echo tasks/prep/101.md; }; stall_watch() { while sleep 1; do :; done; }
  team_prep_basis() { echo "1 step(s) before it could start"; }; team_prep_prompt() { echo prompt; }; team_prep_finish() { :; }
  team_carve_prompt() { echo prompt; }; team_carve_finish() { CARVE_RESULT=none; }
  team_prep_watch() { while sleep 1; do echo beat >> "$T/prep.log"; done; }
  team_carve_watch() { while sleep 1; do echo beat >> "$T/carve.log"; done; }
  : > "$T/prep.log"; : > "$T/carve.log"
  prep_iteration tasks/101-leaf.md
  if alive prep; then echo "FAIL the prep session's watcher still runs after the session"; else echo "ok   the prep session's watcher ended with it"; fi
  CARVE_KEY=102 CARVE_HOLDER=b CARVE_FILE=$T/p/tasks/102-root.md carve_iteration
  if alive carve; then echo "FAIL the carve session's watcher still runs after the session"; else echo "ok   the carve session's watcher ended with it"; fi
  kill $(jobs -p) 2>/dev/null   # watchers the old driver leaves behind
) > "$T/out" 2>&1
grep -E '^(ok|FAIL) ' "$T/out"
grep -q '^FAIL' "$T/out" && fail=1
grep -c '^ok ' "$T/out" | grep -qx 2 || { [ $fail = 1 ] || { echo "FAIL the test did not run both sessions: $(tail -3 "$T/out")"; fail=1; }; }
exit $fail
