#!/usr/bin/env bash
# test_single_regression.sh OLD_DRIVER_DIR NEW_DRIVER_DIR : the single-agent stub scenario (setup.sh: subtasks, a
# rejected done claim, a dropped criterion, a proposal, an agent task, the sibling-reds ratchet, PAUSE) run with two
# driver versions; the loop logs and git histories must match once timestamps and harness ids are removed.
# Use it after any driver change that is meant to leave single-agent projects unchanged (e.g. team mode).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
run() {  # run <name> <driver dir>
  local H=$T/$1/home P=$T/$1/proj
  mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit"
  for f in agent-loop task-audit codemap-gen decisions-archive; do cp "$2/$f" "$H/bin/"; done
  [ -f "$2/agent-team-lib" ] && cp "$2/agent-team-lib" "$H/bin/"
  cp "$HERE/stubpi" "$H/.npm-global/bin/pi"; chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
  printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
  ( export HOME=$H PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin" TEST_CMD="bash fake-test.sh" LLM_URL=$LLM
    bash "$HERE/setup.sh" "$P" > /dev/null 2>&1
    cd "$P" && timeout 600 agent-loop "$P" > /dev/null 2>&1 )
  sed -E 's/^[0-9-]+ [0-9:]+ //; s/harness [0-9a-f]{12}/harness X/; s/\(after iteration [0-9]+\)//' "$P/.agent/loop.log" > "$T/$1.log"
  git -C "$P" log --format=%s > "$T/$1.git"
  for f in "$P"/tasks/*.md; do echo "== $(basename "$f")"; cat "$f"; done > "$T/$1.tasks"
}
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
LLM=http://127.0.0.1:$MS
run old "$1"; run new "$2"
kill $SRV 2>/dev/null
fail=0
for k in log git tasks; do
  if diff "$T/old.$k" "$T/new.$k" > "$T/$k.diff"; then echo "ok   same $k ($(wc -l < "$T/new.$k") lines)"
  else echo "FAIL $k differs:"; head -20 "$T/$k.diff"; fail=1; fi
done
grep -c 'REJECTED' "$T/new.log" | xargs echo "     rejected claims in the scenario:"
exit $fail
