#!/usr/bin/env bash
# test_pitfalls_sync.sh [DRIVER_DIR] : pitfalls-sync and decisions-archive --show, the PITFALLS.md memory layout.
# pitfalls-sync does nothing without PITFALLS.md; moves PITFALL/LESSON entries out of DECISIONS.md into Unsorted with
# their text unchanged (a fenced "## " line stays inside its entry); regenerates the contents block (counts,
# subsections, entries lacking Where:/Symptom:); is idempotent; repairs a contents block doubled by a union merge;
# brings back a deleted fixed section. --show prints a task's own entries (a subtask's parent's too), cuts a long
# entry with the grep that finds it, leaves out PITFALLs and other tasks, and adds the newest 3 driver notes.
set -u
D=$(cd "${1:-$HOME/bin}" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
SYNC="python3 $D/pitfalls-sync"; ARCH="python3 $D/decisions-archive"
TEMPLATE=${TEMPLATE:-$HERE/../../harness/template/PITFALLS.md}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cd "$T" && mkdir tasks
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
printf 'Status: in-progress\n# 004\n' > tasks/004-store.md
printf 'Status: open\n# 004a\n' > tasks/004a-store-part.md
printf 'Status: open\n# 005\n' > tasks/005-web.md
{ echo "# DECISIONS"; echo
  echo "## 2026-10-02 000 PITFALL: pip is blocked"; echo "use apt"; echo
  echo "## 2026-10-02 004 DECISION: store api"; echo "why store"; echo
  echo "## 2026-10-02 004 LESSON: sqlite DDL autocommits"; echo '```'; echo "## not a heading, inside a fence"; echo '```'; echo "after the fence"; echo
  echo "## 2026-10-02 005 DECISION: web layout"; echo "why web"; echo
  echo "## 2026-10-02 DRIVER FLAKY: test_a flaked"; echo "rerun passed"; echo
  echo "## 2026-10-03 004a DECISION: part one"; python3 -c 'print(("x" * 100 + "\n") * 60)' ; echo
  echo "## 2026-10-03 DRIVER: suite failing at loop start (2 tests)"; echo "t1, t2"; echo
} > DECISIONS.md
cp DECISIONS.md DECISIONS.orig

$SYNC . ; rc=$?
[ $rc -eq 0 ] && cmp -s DECISIONS.md DECISIONS.orig && [ ! -e PITFALLS.md ] && ok "no PITFALLS.md: nothing changes (rc 0)" || bad "without PITFALLS.md rc $rc / files changed"

cp "$TEMPLATE" PITFALLS.md
$SYNC . ; rc=$?
[ $rc -eq 3 ] && ok "first run changed the files (rc 3)" || bad "first run rc $rc"
! grep -q "PITFALL\|LESSON" DECISIONS.md && grep -q "004 DECISION: store api" DECISIONS.md && grep -q "DRIVER FLAKY" DECISIONS.md \
  && ok "PITFALL/LESSON entries left DECISIONS.md, the rest stayed" || bad "DECISIONS.md content wrong"
grep -q "^#### 2026-10-02 000 PITFALL: pip is blocked" PITFALLS.md && grep -q "^#### 2026-10-02 004 LESSON: sqlite DDL autocommits" PITFALLS.md \
  && ok "moved with #### headings" || bad "moved headings missing"
python3 - <<'PY' && ok "bodies unchanged, the fenced ## line inside its entry" || bad "moved body differs"
t = open("PITFALLS.md").read()
want = "#### 2026-10-02 004 LESSON: sqlite DDL autocommits\n```\n## not a heading, inside a fence\n```\nafter the fence"
assert want in t and t.index("## Unsorted") < t.index(want)
PY
grep -q "^- Unsorted (2) - moved here from DECISIONS.md" PITFALLS.md && grep -q "^2 entries lack a \`Where:\` or \`Symptom:\` line" PITFALLS.md \
  && ok "contents: Unsorted (2), 2 lacking keys" || bad "contents block wrong: $(sed -n '/contents:start/,/contents:end/p' PITFALLS.md)"
[ "$(sed -n '1,/contents:end/p' PITFALLS.md | wc -l)" -lt 15 ] && ok "contents block is short ($(sed -n '1,/contents:end/p' PITFALLS.md | wc -l) lines to read)" || bad "contents block too long"
$SYNC . ; rc=$?
[ $rc -eq 0 ] && ok "second run: nothing to change (rc 0)" || bad "second run rc $rc"

# an agent files an entry with its keys under a library subsection
python3 - <<'PY'
t = open("PITFALLS.md").read()
t = t.replace("## This codebase", "### gunicorn\n\n#### Arbiter keeps WORKERS as a class attribute\nWhere: tests/service_boot.py, gunicorn 20.1.0\nSymptom: a second boot hangs\nshared by every instance\n\n## This codebase", 1)
open("PITFALLS.md", "w").write(t)
PY
$SYNC . > /dev/null
grep -q "^- Libraries and frameworks (1): gunicorn 1" PITFALLS.md && grep -q "^2 entries lack" PITFALLS.md \
  && ok "contents: subsection counted, keyed entry not flagged" || bad "subsection count wrong"

# a union merge of two agents' copies doubles lines inside the block
python3 - <<'PY'
t = open("PITFALLS.md").read()
a, b = t.index("<!-- contents:start"), t.index("<!-- contents:end -->") + len("<!-- contents:end -->")
open("PITFALLS.md", "w").write(t[:b] + "\n- Unsorted (9) stale\n" + t[a:b] + t[b:])
PY
$SYNC . > /dev/null
[ "$(grep -c 'contents:start' PITFALLS.md)" -eq 1 ] && ! grep -q "stale" PITFALLS.md && ok "doubled contents block repaired" || bad "contents block not repaired"

# a deleted fixed section comes back
python3 - <<'PY'
t = open("PITFALLS.md").read()
a = t.index("## This machine and its environment"); b = t.index("## Libraries and frameworks")
open("PITFALLS.md", "w").write(t[:a] + t[b:])
PY
$SYNC . > /dev/null
grep -q "^## This machine and its environment" PITFALLS.md && [ "$(grep -c '^#### ' PITFALLS.md)" -eq 3 ] \
  && ok "deleted fixed section back, entries intact (3)" || bad "fixed section / entries wrong"

# a team merge brings back an entry PITFALLS.md already has: it only leaves DECISIONS.md
n0=$(grep -c '^#### ' PITFALLS.md)
printf '\n## 2026-10-02 000 PITFALL: pip is blocked\nuse apt\n' >> DECISIONS.md
$SYNC . > /dev/null
! grep -q "PITFALL: pip is blocked" DECISIONS.md && [ "$(grep -c '^#### ' PITFALLS.md)" -eq "$n0" ] \
  && ok "merged-back copy: removed from DECISIONS.md, not added twice" || bad "merged-back copy duplicated"
# the same heading with a different body is still moved (nothing is lost)
printf '\n## 2026-10-02 000 PITFALL: pip is blocked\nuse apt, or the project venv\n' >> DECISIONS.md
$SYNC . > /dev/null
[ "$(grep -c '^#### 2026-10-02 000 PITFALL: pip is blocked' PITFALLS.md)" -eq 2 ] && grep -q "or the project venv" PITFALLS.md \
  && ok "same heading, new body: moved" || bad "changed entry lost"
# an entry an agent deleted (git history) and a merge brought back is not added again
git init -q . && git -c user.email=t@t -c user.name=t add -A && git -c user.email=t@t -c user.name=t commit -qm base
python3 - <<'PY'
t = open("PITFALLS.md").read()
a = t.index("#### 2026-10-02 004 LESSON: sqlite DDL autocommits"); b = t.index("after the fence", a) + len("after the fence")
open("PITFALLS.md", "w").write(t[:a] + t[b:])
PY
$SYNC . > /dev/null; git -c user.email=t@t -c user.name=t commit -qam "agent merged the sqlite entry away"
n1=$(grep -c '^#### ' PITFALLS.md)
printf '\n## 2026-10-02 004 LESSON: sqlite DDL autocommits\n```\n## not a heading, inside a fence\n```\nafter the fence\n' >> DECISIONS.md
$SYNC . > /dev/null
! grep -q "LESSON: sqlite" DECISIONS.md && ! grep -q "LESSON: sqlite" PITFALLS.md && [ "$(grep -c '^#### ' PITFALLS.md)" -eq "$n1" ] \
  && ok "entry deleted by an agent, brought back by a merge: not added again" || bad "deleted entry came back"

# --show: the task's own journal for PROGRESS.md
$ARCH --show 004a 004 > show.txt
grep -q "^### 2026-10-02 004 DECISION: store api" show.txt && grep -q "^### 2026-10-03 004a DECISION: part one" show.txt \
  && ok "--show: own and parent's entries, headings one level down" || bad "--show own entries missing"
! grep -q "005 DECISION\|PITFALL\|LESSON" show.txt && ok "--show: other tasks and PITFALLs left out" || bad "--show leaks other entries"
grep -q "KB more: \`grep -n -A 200 -F '## 2026-10-03 004a DECISION: part one' DECISIONS.md\`" show.txt && [ "$(wc -c < show.txt)" -lt 6000 ] \
  && ok "--show: long entry cut, with its grep ($(wc -c < show.txt) bytes)" || bad "--show cut wrong ($(wc -c < show.txt) bytes)"
grep -q "Project-wide driver notes" show.txt && grep -q "DRIVER FLAKY" show.txt && grep -q "suite failing at loop start" show.txt \
  && ok "--show: driver notes" || bad "--show driver notes missing"
$ARCH --show 999 | grep -q "(no entries yet)" && ok "--show: a task without entries says so" || bad "--show empty case"
exit $fail
