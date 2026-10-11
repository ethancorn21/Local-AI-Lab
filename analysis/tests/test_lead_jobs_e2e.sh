#!/usr/bin/env bash
# test_lead_jobs_e2e.sh [DRIVER_DIR] [TOOLS_DIR] : Lab v2 phase 1 end to end (docs/lab-v2.md), two stub agents, real
# loops, the real `lab` (about 3-6 minutes). Run on the VM.
#   A: the lead takes the project and gives a the plan (000) as a job; b, with no job, waits for one and takes nothing
#   B: a's next job is 003; a builds 003 only (001 and 002, first in the team's pick order, stay open), reports the
#      job's end in the lead journal and waits
#   C: the lead releases the project: the team rules take over and finish the rest
# The old driver ignores the job and the lead: a's job never ends in the journal, and b takes work.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}
TOOLS=${2:-$DRIVER/../tools}
T=$(mktemp -d); trap 'kill $SRV 2>/dev/null; agent-team stop lj --now > /dev/null 2>&1; rm -rf "$T"' EXIT
H=$T/home; PR=$H/projects; mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit/agents" "$PR"
for f in agent-loop agent-team-lib agent-team task-audit codemap-gen decisions-archive plan-schedule pitfalls-sync team-takeover team-carve; do [ -f "$DRIVER/$f" ] && cp "$DRIVER/$f" "$H/bin/"; done
for f in agent-start agent-stop lab; do cp "$TOOLS/$f" "$H/bin/"; done
cp "$HERE/stubpi-team" "$H/.npm-global/bin/pi"
printf 'cat > /dev/null; echo sent\n' > "$H/bin/ring-doorbell"
chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
sudo cp -r /home/agent/.agent-kit/template "$H/.agent-kit/" && sudo chown -R "$(id -u)" "$H/.agent-kit/template"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
: > "$H/.agent-kit/agents/a.env"; : > "$H/.agent-kit/agents/b.env"
echo lj > "$H/.agent-kit/lead-projects"
FX=$T/fixtures; mkdir -p "$FX"
task() { printf 'Status: open\n# %s\n\nDepends on: none\nTouches: %s\n\n## Acceptance criteria\n- [ ] done\n' "${1%.md}" "$2" > "$FX/$1"; }
task 001-first.md "src/first/"; task 002-second.md "src/second/"; task 003-third.md "src/third/"
export HOME=$H STUB_FIXTURES=$FX STUB_SLEEP=2 TEAM_WAIT_S=3 LEAD_POLL_S=2 TEST_CMD=true NO_PROGRESS_LIMIT=20
export PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin"
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
export LLM_URL=http://127.0.0.1:$MS
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
J=$H/.agent-kit/lead/journal.jsonl
ended() { jq -r 'select(.event == "job_ended") | "\(.task) \(.detail)"' "$J" 2>/dev/null | grep -q "^tasks/$1[^ ]* done$"; }
ended_any() { jq -r 'select(.event == "job_ended") | "\(.task) \(.detail)"' "$J" 2>/dev/null | grep "^tasks/$1" | tail -1; }
until_() { local i; for i in $(seq 1 "$1"); do sleep 3; eval "$2" && return 0; done; return 1; }
claims_by() { grep '"event":"claim"' "$PR/lj/.agent/team/events.jsonl" 2>/dev/null | grep "\"agent\":\"$1\"" | jq -r .task | sort -u | tr '\n' ' '; }
st() { head -1 "$PR/lj/$1" 2>/dev/null; }

mkdir -p "$PR/lj"; echo "# Test goal" > "$PR/lj/GOAL.md"; agent-team init lj a b > /dev/null

# A: the plan as a's job; b waits
lab lead take lj > /dev/null
lab run lj a 000 --why "plan first" > /dev/null
agent-start lj.b > /dev/null
until_ 60 'ended 000' && ok "A: a's job 000 ended done (journal)" || bad "A: no 'done' end for a's job 000: $(tail -3 "$J" 2>/dev/null)"
jq -r 'select(.event == "waiting") | .who' "$J" 2>/dev/null | grep -qx "worker b" && ok "A: b, with no job, waits for the lead" || bad "A: b never waited for the lead"
[ -z "$(claims_by b)" ] && ok "A: b took nothing" || bad "A: b claimed: $(claims_by b)"

# B: 003 as a's next job; 001 and 002 stay open
until_ 20 '[ -f "$PR/lj/tasks/003-third.md" ]' || bad "B: the plan did not put the tasks in main"
# the agent renamed its task file on its branch (ancient-works 2026-10-11: 204-van-deventer.md -> 204-van_deventer.md):
# the job follows the task by number
git -C "$PR/lj.a" mv tasks/003-third.md tasks/003-third-renamed.md && git -C "$PR/lj.a" commit -qm "rename 003"
lab run lj a 003 --why "the third one first" > /dev/null
until_ 60 'ended 003' && ok "B: a's job 003 ended done, its file renamed on a's branch (journal)" || bad "B: a's job 003: $(ended_any 003)"
[ "$(st tasks/003-third-renamed.md)" = "Status: done" ] && [ -e "$PR/lj/src/third" ] && ok "B: 003 done and merged into main" || bad "B: 003 in main: '$(st tasks/003-third-renamed.md)'"
sleep 8
[ "$(st tasks/001-first.md)" = "Status: open" ] && [ "$(st tasks/002-second.md)" = "Status: open" ] && ok "B: 001 and 002 untouched while the lead leads" || bad "B: 001 '$(st tasks/001-first.md)', 002 '$(st tasks/002-second.md)'"
[ "$(claims_by a | sed 's/003-third-renamed/003-third/')" = "tasks/000-plan.md tasks/003-third.md " ] && ok "B: a claimed only its jobs" || bad "B: a claimed: $(claims_by a)"
lab log 50 | grep -q "lead claude .* run .* 003-third" && ok "B: lab log shows the lead's run of 003" || bad "B: lab log: $(lab log 5)"

# B2: the lead writes a new task into main while a waits, then gives it to a (ancient-works 2026-10-11: 302 was
# committed to main after c had started waiting; c's checkout never synced, and the job ended "no such task")
sed -e 's/003-third/004-fourth/; s#src/third/#src/fourth/#' "$FX/003-third.md" > "$PR/lj/tasks/004-fourth.md"
git -C "$PR/lj" add tasks/004-fourth.md && git -C "$PR/lj" commit -qm "[lead] 004"
sleep 6
lab run lj a 004 --why "written while a waited" > /dev/null
until_ 60 'ended 004' && ok "B2: a's job 004, written into main while a waited, ended done" || bad "B2: a's job 004: $(ended_any 004)"

# C: release - the team rules finish the rest
lab lead release lj > /dev/null
until_ 60 '[ "$(st tasks/001-first.md)" = "Status: done" ] && [ "$(st tasks/002-second.md)" = "Status: done" ]' \
  && ok "C: after the release the team rules built 001 and 002" || bad "C: 001 '$(st tasks/001-first.md)', 002 '$(st tasks/002-second.md)'"
exit $fail
