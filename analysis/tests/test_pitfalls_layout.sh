#!/usr/bin/env bash
# test_pitfalls_layout.sh DRIVER_DIR : the single-agent stub scenario (setup.sh) in a project with PITFALLS.md, the
# memory layout where DECISIONS.md and PLAN.md are searched instead of read whole. The driver must: move a PITFALL
# the agent wrote to DECISIONS.md into PITFALLS.md; give each session its task's own journal in PROGRESS.md, so the
# session after a rejected done claim reads why; never send the "read DECISIONS.md to the end" nudge or log the 50 KB
# warning for DECISIONS.md, even when it is big; record PITFALLS.md's size in the ledger.
set -u
HERE=$(cd "$(dirname "$0")" && pwd); D=$(cd "$1" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
H=$T/home P=$T/proj
mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit"
for f in agent-loop task-audit codemap-gen decisions-archive pitfalls-sync agent-team-lib; do cp "$D/$f" "$H/bin/"; done
cp "$HERE/stubpi" "$H/.npm-global/bin/pi"; chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
( export HOME=$H PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin" TEST_CMD="bash fake-test.sh" LLM_URL=http://127.0.0.1:$MS
  bash "$HERE/setup.sh" "$P" > /dev/null 2>&1
  cd "$P"
  cp "$HERE/../../harness/template/PITFALLS.md" .
  # a big entry that stays (its task has no file here): the old layout would nudge "read it to the end" and warn
  { echo; echo "## 2026-09-26 777 DECISION: other branch"; python3 -c 'print(("y" * 99 + "\n") * 600)'; } >> DECISIONS.md
  printf '\n## 2026-09-26 000 PITFALL: pip is blocked\nuse apt\n' >> DECISIONS.md
  git add -A && git commit -qm "pitfalls layout"
  # the first agent session also writes a PITFALL to DECISIONS.md, the old habit
  sed -i '1a printf "\\n## 2026-09-27 001 PITFALL: the fake suite reads failing.txt\\nWhere: fake-test.sh\\nSymptom: tests fail\\n" >> DECISIONS.md' .stub/1.sh
  timeout 600 agent-loop "$P" > /dev/null 2>&1 )
kill $SRV 2>/dev/null
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
cd "$P"
! grep -q "PITFALL" DECISIONS.md && grep -q "^#### 2026-09-26 000 PITFALL: pip is blocked" PITFALLS.md \
  && grep -q "^#### 2026-09-27 001 PITFALL: the fake suite reads failing.txt" PITFALLS.md \
  && ok "both PITFALLs moved to PITFALLS.md (start-up and after a session)" || bad "PITFALL not moved"
grep -q "PITFALLS.md synced (start-up)" .agent/loop.log && grep -q "PITFALLS.md synced (after iteration" .agent/loop.log \
  && ok "loop log records both syncs" || bad "sync lines missing from loop log"
hist=$(git log -p --format='=== %s' -- PROGRESS.md)
grep -q "^+## Your task's journal" <<<"$hist" && ok "PROGRESS.md carries the task's journal" || bad "no journal in PROGRESS.md"
python3 - "$P" <<'PY' && ok "the session after a rejected done claim got the reason in PROGRESS.md" || bad "rejection reason not in the next PROGRESS.md"
import re, subprocess, sys
log = subprocess.run(["git", "-C", sys.argv[1], "log", "--reverse", "--format=%H %s"], capture_output=True, text=True).stdout.splitlines()
rej = [i for i, l in enumerate(log) if "reopened" in l or "rejected" in l]
assert rej, "scenario has no rejection"
for h, _ in (l.split(" ", 1) for l in log[rej[0]:]):
    p = subprocess.run(["git", "-C", sys.argv[1], "show", f"{h}:PROGRESS.md"], capture_output=True, text=True).stdout
    j = p.split("## Your task's journal", 1)
    if len(j) == 2 and "DRIVER: done claim rejected" in j[1]:
        sys.exit(0)
sys.exit(1)
PY
! grep -l "read it to the end" .stub/prompt-*.txt > /dev/null 2>&1 && ok "no read-to-the-end nudge in any prompt (DECISIONS.md $(( $(wc -c < DECISIONS.md) / 1024 )) KB)" || bad "nudge sent"
! grep -q "WARNING: DECISIONS.md is" .agent/loop.log && ok "no 50 KB warning for DECISIONS.md" || bad "50 KB warning logged"
jq -e '.memory_bytes["PITFALLS.md"] > 0' .agent/iterations.jsonl > /dev/null 2>&1 && ok "ledger records PITFALLS.md size" || bad "ledger lacks PITFALLS.md"
exit $fail
