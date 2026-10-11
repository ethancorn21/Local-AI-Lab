#!/usr/bin/env bash
# test_session_tmp_e2e.sh [DRIVER_DIR] : a loop's temp files do not outlive it (agent-loop, TMPDIR). ancient-works
# 2026-10-11: the VM's disk filled (pytest kept every run's copies of a 549 MB data tree; the agents' smoke-test folders
# were never removed) and every agent's sessions died at once. One stub agent, a real loop (about 1-2 minutes); each
# session leaves a 1 MB temp folder behind, the way a test or a smoke run does. Checked: they were made in the loop's
# own temp folder, and none is left once the loop has stopped. The old driver leaves them all in /tmp. Run on the VM.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}
T=$(mktemp -d); trap 'kill $SRV 2>/dev/null; agent-team stop tp --now > /dev/null 2>&1; xargs -r rm -rf < "$T/made" 2>/dev/null; rm -rf "$T" /tmp/loop-tp.a' EXIT
H=$T/home; PR=$H/projects; mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit/agents" "$PR"
for f in agent-loop agent-team-lib agent-team task-audit codemap-gen decisions-archive plan-schedule pitfalls-sync team-takeover team-carve; do [ -f "$DRIVER/$f" ] && cp "$DRIVER/$f" "$H/bin/"; done
for f in agent-start agent-stop; do cp "$DRIVER/../tools/$f" "$H/bin/"; done
cp "$HERE/stubpi-team" "$H/.npm-global/bin/pi-real"
printf '#!/bin/sh\nd=$(mktemp -d) && head -c 1048576 /dev/zero > "$d/junk" && echo "$d" >> %s/made\nexec "$(dirname "$0")/pi-real" "$@"\n' "$T" > "$H/.npm-global/bin/pi"
printf 'cat > /dev/null; echo sent\n' > "$H/bin/ring-doorbell"
chmod +x "$H/bin/"* "$H/.npm-global/bin/"*
sudo cp -r /home/agent/.agent-kit/template "$H/.agent-kit/" && sudo chown -R "$(id -u)" "$H/.agent-kit/template"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
: > "$H/.agent-kit/agents/a.env"; : > "$T/made"
FX=$T/fixtures; mkdir -p "$FX"
task() { printf 'Status: open\n# %s\n\nDepends on: none\nTouches: %s\n\n## Acceptance criteria\n- [ ] done\n' "${1%.md}" "$2" > "$FX/$1"; }
task 001-first.md "src/first/"; task 002-second.md "src/second/"
export HOME=$H STUB_FIXTURES=$FX STUB_SLEEP=1 TEAM_WAIT_S=3 TEST_CMD=true NO_PROGRESS_LIMIT=20
export PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin"
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
export LLM_URL=http://127.0.0.1:$MS
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }

mkdir -p "$PR/tp"; echo "# Test goal" > "$PR/tp/GOAL.md"; agent-team init tp a > /dev/null && agent-team start tp > /dev/null
for i in $(seq 1 60); do sleep 4; [ -f "$PR/tp/.agent/team/STOP" ] && ! pgrep -u "$(id -u)" -f "agent-loop $PR/tp" > /dev/null && break; done
n=$(grep -c . "$T/made")
[ "$n" -ge 3 ] && ok "the stub ran $n sessions" || bad "only $n sessions ran: $(tail -3 "$PR/tp.a/.agent/loop.log")"
out=$(grep -vc '^/tmp/loop-tp\.a/' "$T/made")
[ "$out" = 0 ] && ok "every session's temp folder was in the loop's own folder" || bad "$out of $n outside /tmp/loop-tp.a: $(grep -v '^/tmp/loop-tp\.a/' "$T/made" | head -2 | tr '\n' ' ')"
left=$(while read -r d; do [ -e "$d" ] && echo "$d"; done < "$T/made" | grep -c .)
[ "$left" = 0 ] && ok "none of them is left after the loop stopped" || bad "$left of $n temp folders left behind"
exit $fail
