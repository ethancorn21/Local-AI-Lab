#!/usr/bin/env bash
# test_bg_verify_e2e.sh [DRIVER_DIR] [TOOLS_DIR] : background verify (BG_VERIFY=1, agent-team-lib) end to end, one stub
# agent in job mode under a lead, real loop, the real `lab`, a test command that takes GATE_S seconds (about 3-5
# minutes). Run on the VM.
#   A: a's job 001 ends as soon as its boxes check out; its suite runs in the background, and a's session on its next
#      job (002) starts while that run is still going
#   B: the run fails (001's build left a BROKEN file): a finishes 002, then builds 001 again with no new job from the
#      lead, and the lead journal says why
#   C: the fix passes: main gets 001 and 002 (002 went in with the fix's run, which tested its commits too), both
#      done in main, both claims released, every merge in main tested
#   D: 002 started on an untested commit, and no test run held its first session (no foreground baseline run)
#   E: a restart while 003's run is going (agent-stop --now kills the worker with the loop): the new loop runs no
#      start-up suite, and its worker tests the queue left behind; 003 reaches main
# The suite also needs a file in data/raw/, which git ignores and only the agent's checkout has (as ancient-works' tests
# need its data): the background run sees it through the worktree's links.
# The old driver tests in the foreground: 002's session starts only after 001's gate, and nothing is queued.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}
TOOLS=${2:-$DRIVER/../tools}
T=$(mktemp -d); trap 'kill $SRV 2>/dev/null; agent-team stop bv --now > /dev/null 2>&1; pkill -f "$T/gate.sh" 2>/dev/null; rm -rf "$T"' EXIT
H=$T/home; PR=$H/projects; mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit/agents" "$H/.agent-kit/projects" "$PR"
for f in agent-loop agent-team-lib agent-team task-audit codemap-gen decisions-archive plan-schedule pitfalls-sync team-takeover team-carve; do [ -f "$DRIVER/$f" ] && cp "$DRIVER/$f" "$H/bin/"; done
for f in agent-start agent-stop lab; do cp "$TOOLS/$f" "$H/bin/"; done
cp "$HERE/stubpi-team" "$H/.npm-global/bin/pi"
printf 'cat > /dev/null; echo sent\n' > "$H/bin/ring-doorbell"
chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
sudo cp -r /home/agent/.agent-kit/template "$H/.agent-kit/" && sudo chown -R "$(id -u)" "$H/.agent-kit/template"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
: > "$H/.agent-kit/agents/a.env"
echo bv > "$H/.agent-kit/lead-projects"
echo BG_VERIFY=1 > "$H/.agent-kit/projects/bv.env"
# the suite: GATE_S seconds, fails while a file under src/ says BROKEN (pytest's FAILED line, which the driver names)
GATE_S=${GATE_S:-20}
printf '#!/bin/bash\nsleep %s\n[ -f data/raw/needed.txt ] || { echo "FAILED tests/test_gate.py::test_data - data/raw missing"; exit 1; }\nif grep -rqs BROKEN src/; then echo "FAILED tests/test_gate.py::test_nothing_broken - BROKEN in src"; exit 1; fi\necho "1 passed"\n' "$GATE_S" > "$T/gate.sh"
chmod +x "$T/gate.sh"
FX=$T/fixtures; mkdir -p "$FX"
task() { printf 'Status: open\n# %s\n\nDepends on: none\nTouches: %s\n%s\n## Acceptance criteria\n- [ ] done\n' "${1%.md}" "$2" "${3:-}" > "$FX/$1"; }
task 001-first.md "src/one/" "Stub-breaks: src/one/BROKEN"; task 002-second.md "src/two/"; task 003-third.md "src/three/"
export HOME=$H STUB_FIXTURES=$FX STUB_SLEEP=2 TEAM_WAIT_S=3 LEAD_POLL_S=2 TEST_CMD=$T/gate.sh NO_PROGRESS_LIMIT=20
export PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin"
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
export LLM_URL=http://127.0.0.1:$MS
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
J=$H/.agent-kit/lead/journal.jsonl
L=$PR/bv.a/.agent/loop.log
ended() { jq -r 'select(.event == "job_ended") | .task' "$J" 2>/dev/null | grep -q "^tasks/$1"; }
jev() { jq -r "select(.event == \"$1\") | .task" "$J" 2>/dev/null | grep -c "^tasks/$2"; }
until_() { local i; for i in $(seq 1 "$1"); do sleep 3; eval "$2" && return 0; done; return 1; }
st() { head -1 "$PR/bv/$1" 2>/dev/null; }
at() { grep -m1 "$1" "$L" | cut -c1-19; }   # first loop.log line matching, its time

mkdir -p "$PR/bv"; echo "# Test goal" > "$PR/bv/GOAL.md"; agent-team init bv a > /dev/null
# data the suite reads that git ignores (ancient-works: data/raw/, web/data/sites/*): in the agent's checkout only
mkdir -p "$PR/bv.a/data/raw"; echo x > "$PR/bv.a/data/raw/needed.txt"
echo 'data/raw/' >> "$(git -C "$PR/bv.a" rev-parse --git-common-dir)/info/exclude"   # a directory pattern, as there
lab lead take bv > /dev/null
lab run bv a 000 --why "plan" > /dev/null
agent-start bv.a > /dev/null
until_ 60 'ended 000' || bad "setup: a's job 000 did not end: $(tail -3 "$J" 2>/dev/null)"
lab run bv a 001 --why "the one that breaks" > /dev/null
until_ 60 'ended 001' || bad "setup: a's job 001 did not end"
lab board | grep -q "testing in the background: .*001" && ok "A: lab board shows 001 testing in the background" || bad "A: lab board: $(lab board | grep -A3 '^  a ')"
lab run bv a 002 --why "the next one" > /dev/null

# A: 002 starts while 001 is tested
until_ 60 'grep -q "iteration [0-9]*: tasks/002" "$L"' || bad "A: a never started 002"
q=$(at "tasks/001-first.md queued for its test run in the background")
s2=$(at "iteration [0-9]*: tasks/002")
until_ 80 'grep -q "background test run rejected tasks/001" "$L"' || bad "A/B: the background run of 001 never failed: $(grep -E '001' "$L" | tail -3)"
r1=$(at "background test run rejected tasks/001")
# its run takes at least GATE_S seconds: a session that starts sooner after the queueing ran beside it
if [ -n "$q" ] && [ -n "$s2" ] && [ $(( $(date -d "$s2" +%s) - $(date -d "$q" +%s) )) -lt "$GATE_S" ]; then ok "A: 001 queued at ${q:11}, 002's session started at ${s2:11}, while 001's ${GATE_S} s run went on (rejected ${r1:11})"
else bad "A: 002 did not start while 001 was tested (queued '$q', 002 started '$s2', 001 rejected '$r1')"; fi

# B: after 002, 001 again, with no job from the lead
until_ 60 'grep -q "failed its background test run - building it again" "$L"' && ok "B: a went back to 001 on its own" || bad "B: a did not go back to 001"
[ "$(jev verify_failed 001)" -ge 1 ] && ok "B: the lead journal has 001's failed run" || bad "B: no verify_failed for 001 in the journal"
grep -n -E "iteration [0-9]+: tasks/00[12]" "$L" | tail -2 | sed -E 's/.*: (tasks\/[^ ]+).*/\1/' | tr '\n' ' ' | grep -q "002-second.md tasks/001-first.md" \
  && ok "B: 002 finished first, then 001" || bad "B: the order of the last sessions: $(grep -E "iteration [0-9]+: tasks/00[12]" "$L" | tail -3 | cut -c21-70 | tr '\n' '|')"

# C: both in main, tested, claims released
until_ 80 '[ "$(st tasks/001-first.md)" = "Status: done" ] && [ "$(st tasks/002-second.md)" = "Status: done" ]' \
  && ok "C: 001 and 002 done in main" || bad "C: main: 001 '$(st tasks/001-first.md)', 002 '$(st tasks/002-second.md)'"
[ -e "$PR/bv/src/one" ] && [ -e "$PR/bv/src/two" ] && [ ! -e "$PR/bv/src/one/BROKEN" ] && ok "C: main has both builds and not the broken file" || bad "C: main's src: $(ls -R "$PR/bv/src" 2>/dev/null | tr '\n' ' ')"
until_ 20 '[ ! -d "$PR/bv/.agent/team/claims/001" ] && [ ! -d "$PR/bv/.agent/team/claims/002" ]' && ok "C: both claims released" || bad "C: claims left: $(ls "$PR/bv/.agent/team/claims")"
[ -f "$PR/bv/.agent/team/tested/$(git -C "$PR/bv" ls-tree -r main | grep -vE $'\t''([^/]+\.md|tasks/.*|codemap/.*)$' | sha256sum | cut -c1-16)" ] \
  && ok "C: main's code has a test record (it went in through the gate)" || bad "C: main's code was never tested"

# D: no foreground baseline run
grep -q "baseline for 002.*first hand-out" "$L" && bad "D: a ran the suite before 002's first session: $(grep 'baseline for 002' "$L")" || ok "D: no baseline run held 002's first session"
# E: restart while a run is queued
lab run bv a 003 --why "stopped while tested" > /dev/null
until_ 60 'ended 003' || bad "E: a's job 003 did not end"
agent-stop bv.a --now > /dev/null
grep -q "003-third.md" "$PR/bv.a/.agent/bgv/queue" && ok "E: 003 still queued after the stop" || bad "E: queue after the stop: $(cat "$PR/bv.a/.agent/bgv/queue" 2>/dev/null)"
n0=$(grep -c "start-up audit" "$L")
agent-start bv.a > /dev/null
until_ 60 '[ "$(st tasks/003-third.md)" = "Status: done" ]' && ok "E: after the restart 003's queued run put it into main" || bad "E: main's 003: '$(st tasks/003-third.md)'"
tail -n +1 "$L" | grep "start-up audit" | tail -1 | grep -q "suite not run" && [ "$(grep -c "start-up audit" "$L")" -gt "$n0" ] \
  && ok "E: the restarted loop ran no start-up suite" || bad "E: start-up audit: $(grep "start-up audit" "$L" | tail -1)"
exit $fail
