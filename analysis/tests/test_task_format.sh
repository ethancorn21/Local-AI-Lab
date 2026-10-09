#!/usr/bin/env bash
# test_task_format.sh DRIVER_DIR TOOLS_DIR : the task-file format (refactor map card 2, 2026-10-08), end to end with the
# scripted single-agent stub (stubpi), no model, about a minute. Run on the VM.
#   1 (001) sets its status to `Status: Done (verified)` with every box ticked, logs a decision, and writes task 003
#     with its dependency under a `## Depends on` heading (what the old format guide showed)
#         -> 001 is verified done and its decision archived (every reader sees "done"); the loop log names 003's heading
#   2 (002) its prompt says to fix 003 and gives the line to write (`Depends on: 001`); it fixes 003 and finishes 002
#   3 (003) its prompt no longer asks for a fix; done.  sprint-progress counts all three done.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}; TOOLS=${2:-$HERE/../../harness/tools}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
H=$T/home P=$T/tf
mkdir -p "$H/bin" "$H/.npm-global/bin"
for f in agent-loop task-audit codemap-gen decisions-archive; do cp "$DRIVER/$f" "$H/bin/"; done
cp "$TOOLS/sprint-progress" "$H/bin/"
cp "$HERE/stubpi" "$H/.npm-global/bin/pi"; chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
export HOME=$H PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p "$P"/{tasks,.stub,.agent}; cd "$P"; git init -q
printf '.agent/\n.stub/\n' > .gitignore; printf '# rules\n' > AGENTS.md; printf '# PROGRESS\n' > PROGRESS.md; printf '# DECISIONS\n' > DECISIONS.md
printf 'Status: open\n# 001: A\n\n## Acceptance criteria\n- [ ] a works\n' > tasks/001-a.md
printf 'Status: open\n# 002: B\n\n## Acceptance criteria\n- [ ] b works\n' > tasks/002-b.md
git add -A; git commit -qm init
cat > .stub/1.sh <<'S'
[ "$TASK" = tasks/001-a.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
printf '\n## 2026-10-08 001 DECISION: chose the simple way\nwhy.\n' >> DECISIONS.md
printf 'Status: open\n# 003: C\n\n## Depends on\n- 001\n\n## Acceptance criteria\n- [ ] c works\n' > tasks/003-c.md
sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: Done (verified)/' "$TASK"; git add -A; git commit -qm "001: done; new task 003"
S
cat > .stub/2.sh <<'S'
[ "$TASK" = tasks/002-b.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
python3 - <<'PY'
import re
t = open("tasks/003-c.md").read()
open("tasks/003-c.md", "w").write(re.sub(r"## Depends on\n- 001\n", "Depends on: 001\n", t))
PY
sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "002: done; 003's dependency line fixed"
S
cat > .stub/3.sh <<'S'
[ "$TASK" = tasks/003-c.md ] || { echo "WRONG TASK $TASK" >&2; exit 1; }
sed -i 's/- \[ \]/- [x]/; 1s/.*/Status: done/' "$TASK"; git add -A; git commit -qm "003: done"
S
TEST_CMD=true LLM_URL=http://127.0.0.1:$MS timeout 300 agent-loop "$P" > /dev/null 2>&1
L=$P/.agent/loop.log
grep -q 'verified done: tasks/001-a.md' "$L" && ok "'Status: Done (verified)' is read as done and verified" || bad "001 not verified: $(grep 001 "$L" | tail -3 | tr '\n' '|')"
grep -q 'chose the simple way' "$P/DECISIONS-archive.md" 2>/dev/null && ok "its decision went to the archive (decisions-archive reads it as done too)" || bad "001's decision not archived"
grep -q 'task format: tasks/003-c.md line 4: `## Depends on` is a heading' "$L" && ok "the loop log names 003's dependency heading" || bad "no format line in the log"
grep -q 'FIRST, fix these task files' "$P/.stub/prompt-2.txt" && grep -q 'Write `Depends on: 001` instead' "$P/.stub/prompt-2.txt" \
  && ok "the next prompt says what to fix and the line to write" || bad "prompt 2: $(cat "$P/.stub/prompt-2.txt" 2>/dev/null | cut -c1-300)"
[ -f "$P/.stub/prompt-3.txt" ] && ! grep -q 'FIRST, fix these task files' "$P/.stub/prompt-3.txt" && ok "fixed: the prompt after that asks nothing" || bad "prompt 3 still asks for a fix (or no session 3)"
d=$(sprint-progress "$P" --json | python3 -c 'import json,sys; p=json.load(sys.stdin); print(p["done"], p["total"])')
[ "$d" = "3 3" ] && ok "sprint-progress: 3 of 3 done" || bad "sprint-progress: $d"
kill $SRV 2>/dev/null
[ $fail -eq 0 ] && echo "ALL OK" || echo "SOME FAILED"
exit $fail
