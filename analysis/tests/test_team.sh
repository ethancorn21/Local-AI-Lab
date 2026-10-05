#!/usr/bin/env bash
# test_team.sh DRIVER_DIR : team mode end to end with two stub agents, no model (about 4-6 minutes). Run on the VM.
# DRIVER_DIR holds agent-loop, agent-team-lib, agent-team, task-audit, codemap-gen, decisions-archive (default: the
# repo's harness/driver next to this file). A private HOME (its own projects folder) keeps it away from the real agents.
# Scenario 1 (tt): planning by one agent while the other waits; parallel work; a dependency honoured (003 after 001 is
#   in main); two tasks touching the same files never held at once (001, 004); every task done in main; the goal check
#   and a clean stop of both loops; only 000/999 registered as the human's; no duplicate task numbers.
# Scenario 4 (tp): prep while a dependency is built, the cut, the notes into main, the hand-off to the claimer.
# Scenario 2 (tc): two tasks that write the same file they did not declare, at the same time: one merge conflict,
#   caught, explained in DECISIONS.md, resolved, both tasks end up in main.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
H=$T/home; PR=$H/projects; mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit/agents" "$PR"
for f in agent-loop agent-team-lib agent-team task-audit codemap-gen decisions-archive; do cp "$DRIVER/$f" "$H/bin/"; done
for f in plan-schedule pitfalls-sync; do [ -f "$DRIVER/$f" ] && cp "$DRIVER/$f" "$H/bin/"; done
for f in agent-start agent-stop; do sudo cat "/home/agent/bin/$f" > "$H/bin/$f"; done
cp "$HERE/stubpi-team" "$H/.npm-global/bin/pi"; chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
sudo cp -r /home/agent/.agent-kit/template "$H/.agent-kit/" && sudo chown -R "$(id -u)" "$H/.agent-kit/template"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
: > "$H/.agent-kit/agents/a.env"; : > "$H/.agent-kit/agents/b.env"
FX=$T/fixtures; mkdir -p "$FX"
task() {  # task <file> <depends> <touches> [extra line]
  printf 'Status: open\n# %s\n\nDepends on: %s\nTouches: %s\n%s\n## Acceptance criteria\n- [ ] done\n' "${1%.md}" "$2" "$3" "${4:-}" > "$FX/$1"
}
export HOME=$H STUB_FIXTURES=$FX STUB_SLEEP=3 TEAM_WAIT_S=3 TEST_CMD=true NO_PROGRESS_LIMIT=20
export PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin"
# the driver waits for a model server: a static one that answers /v1/models
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
export LLM_URL=http://127.0.0.1:$MS
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }
run_team() {  # run_team <name> : init, start, wait until both loops stop after the goal check (max ~8 min)
  mkdir -p "$PR/$1"; echo "# Test goal" > "$PR/$1/GOAL.md"
  agent-team init "$1" a b > /dev/null || { bad "$1: init failed"; return 1; }
  agent-team start "$1" > /dev/null
  for _ in $(seq 1 100); do
    sleep 5
    [ -f "$PR/$1/.agent/team/STOP" ] && ! pgrep -u "$(id -u)" -f "agent-loop $PR/$1" > /dev/null && break
  done
  agent-team status "$1" | sed 's/^/     /'
  [ -f "$PR/$1/.agent/team/STOP" ] && ok "$1: goal check ran and wrote STOP" || bad "$1: no STOP"
  if pgrep -u "$(id -u)" -f "agent-loop $PR/$1" > /dev/null; then bad "$1: loops still running"; agent-team stop "$1" --now > /dev/null; else ok "$1: both loops stopped"; fi
  local nd; nd=$(for f in "$PR/$1"/tasks/[0-9]*.md; do head -1 "$f"; done | grep -vc 'Status: done')
  [ "$nd" -eq 0 ] && ok "$1: every task done in main ($(ls "$PR/$1"/tasks/[0-9]*.md | wc -l) tasks)" || bad "$1: $nd task(s) not done in main"
}

# --- scenario 1 ---
task 001-core.md none "src/core/"
task 002-ui.md none "src/ui/"
task 003-api.md "001" "src/api/"
task 004-core-extra.md none "src/core/extra.txt"
task 005-docs.md "002, 003" "README.md"
run_team tt
M=$PR/tt
for p in src/core src/ui src/api README.md; do [ -e "$M/$p" ] || bad "tt: main lacks $p"; done
python3 - "$M/.agent/team/events.jsonl" <<'PY' || fail=1
import json, sys
ev = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
held, intervals = {}, []   # task key -> (agent, since); (key, agent, start, end)
for i, e in enumerate(ev):
    k = e["task"].split("/")[-1].split("-")[0] if e["task"] else ""
    if e["event"] == "claim":
        held[k] = (e["agent"], i)
    elif e["event"] == "release" and k in held:
        a, s = held.pop(k); intervals.append((k, a, s, i))
intervals += [(k, a, s, len(ev)) for k, (a, s) in held.items()]
bad = 0
def check(cond, msg):
    global bad
    print(("ok   " if cond else "FAIL ") + "tt: " + msg); bad += not cond
check(not any(a[0] == "001" and b[0] == "004" and a[2] < b[3] and b[2] < a[3] for a in intervals for b in intervals),
      "001 and 004 (same files) never held at the same time")
check(any(a[1] != b[1] and a[2] < b[3] and b[2] < a[3] for a in intervals for b in intervals), "the two agents held tasks at the same time")
m001 = min((i for i, e in enumerate(ev) if e["event"] == "merge" and "/001-" in e["task"]), default=None)
c003 = min((i for i, e in enumerate(ev) if e["event"] == "claim" and "/003-" in e["task"]), default=None)
check(m001 is not None and c003 is not None and c003 > m001, "003 claimed only after 001 was merged into main")
c000 = [e["agent"] for e in ev if e["event"] == "claim" and "/000-" in e["task"]]
check(len(set(c000)) == 1, f"planning done by one agent ({c000})")
waits = [e for e in ev if e["event"] == "wait_end"]
check(len(waits) > 0, f"waits recorded ({len(waits)}, {sum(int(e['detail']) for e in waits)} s)")
odd = [(e["agent"], e["event"], e["task"], e["detail"][:80]) for e in ev if e["event"] in ("merge_conflict", "post_merge_fail", "dup_task_id", "merge_failed", "deps_deadlock", "stale_claim")]
check(not odd, f"no conflicts, duplicate ids, failed merges, deadlocks or stale claims {odd if odd else ''}")
sys.exit(1 if bad else 0)
PY
for id in a b; do
  h=$(python3 -c "import json; r=json.load(open('$M.$id/.agent/tasks.json')); print(sorted(k for k,v in r.items() if v['owner']=='human'))")
  case $h in "['000']"|"['000', '999']") ok "tt: agent $id human tasks $h" ;; *) bad "tt: agent $id human tasks $h" ;; esac
done

# --- scenario 1b: GOAL.md changes after the team finished -> 000 re-planned, a second goal-check round, clean stop.
# Both tasks are done in main from round 1 and reopened by the driver; they must not look like a stale view
# (frontpage 2026-10-02: the lead spun refusing and forcing 999).
n0=$(wc -l < "$M/.agent/team/events.jsonl")
(cd "$M" && echo "- one more wish" >> GOAL.md && git commit -qam "[human] GOAL.md: one more wish")
agent-team start tt > /dev/null
for _ in $(seq 1 60); do
  sleep 5
  [ -f "$M/.agent/team/STOP" ] && ! pgrep -u "$(id -u)" -f "agent-loop $PR/tt" > /dev/null && break
done
agent-team stop tt --now > /dev/null 2>&1
python3 - "$M/.agent/team/events.jsonl" "$n0" <<'PY' || fail=1
import json, sys
ev = [json.loads(l) for l in open(sys.argv[1]) if l.strip()][int(sys.argv[2]):]
bad = 0
def check(cond, msg):
    global bad
    print(("ok   " if cond else "FAIL ") + "tt round 2: " + msg); bad += not cond
has = lambda e, t: any(x["event"] == e and t in x["task"] for x in ev)
check(has("claim", "/000-") and has("merge", "/000-"), "GOAL.md change: 000 re-planned and merged")
check(has("claim", "/999-") and has("merge", "/999-"), "a second goal-check round ran and merged")
check(any(x["event"] == "stop" for x in ev), "the team stopped after it")
odd = [(x["event"], x["task"]) for x in ev if x["event"] in ("deps_deadlock", "stale_view", "stale_claim")]
check(not odd, f"no deadlock, stale view or stale claim {odd if odd else ''}")
sys.exit(1 if bad else 0)
PY

# --- scenario 2: two tasks write the same undeclared file at the same time ---
rm -f "$FX"/*.md
task 001-left.md none "src/left/" "Stub-writes: shared.txt"
task 002-right.md none "src/right/" "Stub-writes: shared.txt"
export STUB_SLEEP=8 STUB_NO_STATUS='999-'; run_team tc; unset STUB_NO_STATUS   # also: a goal check whose status is left open must still end
M=$PR/tc
grep -q '"merge_conflict".*shared.txt' "$M/.agent/team/events.jsonl" && ok "tc: the undeclared shared file was caught as a merge conflict" || bad "tc: no merge conflict recorded for shared.txt"
[ -d "$M/src/left" ] && [ -d "$M/src/right" ] && ok "tc: after the conflict both tasks' work is in main" || bad "tc: work missing in main"
grep -qs 'cannot go into main yet' "$M.a/DECISIONS.md" "$M.b/DECISIONS.md" "$M/DECISIONS.md" && ok "tc: the reopened task was told why (DECISIONS.md)" || bad "tc: no conflict reason in DECISIONS.md"
for id in a b; do grep -h "team:" "$PR/tc.$id/.agent/loop.log" | sed "s/^/     $id /" | tail -6; done

# --- scenario 3: both agents stopped mid-task and started again -> each takes back its own task, nothing is stolen
rm -f "$FX"/*.md
task 001-slow-left.md none "src/left/"
task 002-slow-right.md none "src/right/"
export STUB_SLEEP=25
mkdir -p "$PR/tr"; echo "# Restart goal" > "$PR/tr/GOAL.md"
agent-team init tr a b > /dev/null
# The incident (frontpage 2026-10-02): after a restart, the agent that starts first picks the lowest open task, which
# is the OTHER agent's (open in its checkout, its old loop gone). So: b alone plans and takes 001, then a takes 002;
# restart with a first.
claimed() { grep -qE "\"agent\":\"$1\",\"event\":\"claim\",\"task\":\"[^\"]*$2" "$PR/tr/.agent/team/events.jsonl" 2>/dev/null; }
agent-start "$PR/tr.b" > /dev/null
for _ in $(seq 1 60); do sleep 2; claimed b 001-slow && break; done
agent-start "$PR/tr.a" > /dev/null
for _ in $(seq 1 30); do sleep 2; claimed a 002-slow && break; done
claimed b 001-slow && claimed a 002-slow && ok "tr: before the restart b holds 001, a holds 002" || bad "tr: set-up did not reach b=001, a=002"
sleep 3; agent-team stop tr --now > /dev/null; sleep 3; agent-team start tr > /dev/null
for _ in $(seq 1 100); do
  sleep 5
  [ -f "$PR/tr/.agent/team/STOP" ] && ! pgrep -u "$(id -u)" -f "agent-loop $PR/tr" > /dev/null && break
done
python3 - "$PR/tr/.agent/team/events.jsonl" <<'PY' || fail=1
import json, sys
ev = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
bad = 0
def check(cond, msg):
    global bad
    print(("ok   " if cond else "FAIL ") + "tr: " + msg); bad += not cond
stale = [(e["agent"], e["task"]) for e in ev if e["event"] == "stale_claim"]
check(not stale, f"restart: no claim taken over {stale if stale else ''}")
for t in ("001-slow-left", "002-slow-right"):
    who = {e["agent"] for e in ev if e["event"] in ("claim", "merge") and t in e["task"]}
    check(len(who) == 1, f"{t}: claimed and merged by one agent only ({sorted(who)})")
check(any(e["event"] == "merge" and "001-slow" in e["task"] for e in ev) and any(e["event"] == "merge" and "002-slow" in e["task"] for e in ev),
      "both tasks merged after the restart")
sys.exit(1 if bad else 0)
PY
# --- scenario 4: prep and the hand-off. b (slow, 1-file tasks) can never take 001, so while a builds it b prepares
# 002 (the longest chain behind 001); when 001 is merged, b's prep session is cut, its notes go into main, and a,
# claiming 002 at once, waits for them and starts from them. Only the notes survive the prep session.
rm -f "$FX"/*.md
task 001-base.md none "src/base/, src/base2/" $'Stub-sleep: 25\nSplit: no - test fixture'
task 002-next.md "001" "src/next/"
task 003-leaf.md "001" "src/leaf/"
task 004-tail.md "002" "src/tail/"
printf 'TEAM_SPEED=4\n' > "$H/.agent-kit/agents/a.env"; printf 'TEAM_SPEED=1\nTEAM_MAX_TOUCHES=1\n' > "$H/.agent-kit/agents/b.env"
export STUB_SLEEP=3 STUB_PREP_WAIT=120 TEAM_PREP_POLL_S=2 TEAM_PREP_HANDOFF_POLL_S=1
run_team tp
unset STUB_PREP_WAIT TEAM_PREP_POLL_S TEAM_PREP_HANDOFF_POLL_S; : > "$H/.agent-kit/agents/a.env"; : > "$H/.agent-kit/agents/b.env"
M=$PR/tp
git -C "$M" cat-file -e main:tasks/prep/002.md 2>/dev/null && ok "tp: b's prep notes for 002 are in main" || bad "tp: no prep notes in main"
! git -C "$M" log --all --format= --name-only main | grep -q 'prep-stray' && ok "tp: nothing else of the prep session reached main" || bad "tp: prep-stray.txt reached main"
grep -q 'PREP NOTES: tasks/prep/002.md' "$M.a/.agent/stub-prompts.txt" "$M.b/.agent/stub-prompts.txt" 2>/dev/null && ok "tp: the session that built 002 was pointed at the notes" || bad "tp: no PREP NOTES pointer"
jq -s -e 'map(select(.kind == "prep")) | length > 0' "$M.b/.agent/iterations.jsonl" > /dev/null 2>&1 && ok "tp: b's ledger has a prep row" || bad "tp: no prep row in b's ledger ($(ls "$M.b/.agent/" | tr '\n' ' '))"
python3 - "$M/.agent/team/events.jsonl" <<'PY' || fail=1
import json, sys
ev = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
bad = 0
def check(cond, msg):
    global bad
    print(("ok   " if cond else "FAIL ") + "tp: " + msg); bad += not cond
at = lambda e, t, a=None: next((i for i, x in enumerate(ev) if x["event"] == e and t in x["task"] and (a is None or x["agent"] == a)), None)
check(at("claim", "/001-", "a") is not None, "a built 001 (too big for b)")
p, m, c = at("prep_start", "/002-", "b"), at("merge", "/001-"), at("prep_cut", "/002-", "b")
check(p is not None and m is not None and p < m, "b prepared 002 while 001 was being built")
check(c is not None and c > m, "b's prep was cut after 001 was merged")
check(at("prep_published", "/002-", "b") is not None, "the notes were published")
h = [x for x in ev if x["event"] == "prep_handoff"]
u = at("prep_used", "/002-")
check(u is not None, "the agent that built 002 started from the notes" + (f" (hand-off: {h[0]['agent']} {h[0]['detail']})" if h else ""))
odd = [(x["event"], x["task"]) for x in ev if x["event"] in ("prep_publish_failed", "deps_deadlock", "stale_claim", "merge_conflict")]
check(not odd, f"no failed publish, deadlock, stale claim or conflict {odd if odd else ''}")
sys.exit(1 if bad else 0)
PY
for id in a b; do grep -hE "prep|team: waited" "$M.$id/.agent/loop.log" | sed "s/^/     $id /" | tail -8; done

kill $SRV 2>/dev/null
exit $fail
