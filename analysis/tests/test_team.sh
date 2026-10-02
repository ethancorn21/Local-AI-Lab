#!/usr/bin/env bash
# test_team.sh DRIVER_DIR : team mode end to end with two stub agents, no model (about 4-6 minutes). Run on the VM.
# DRIVER_DIR holds agent-loop, agent-team-lib, agent-team, task-audit, codemap-gen, decisions-archive (default: the
# repo's harness/driver next to this file). A private HOME (its own projects folder) keeps it away from the real agents.
# Scenario 1 (tt): planning by one agent while the other waits; parallel work; a dependency honoured (003 after 001 is
#   in main); two tasks touching the same files never held at once (001, 004); every task done in main; the goal check
#   and a clean stop of both loops; only 000/999 registered as the human's; no duplicate task numbers.
# Scenario 2 (tc): two tasks that write the same file they did not declare, at the same time: one merge conflict,
#   caught, explained in DECISIONS.md, resolved, both tasks end up in main.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
H=$T/home; PR=$H/projects; mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit/agents" "$PR"
for f in agent-loop agent-team-lib agent-team task-audit codemap-gen decisions-archive; do cp "$DRIVER/$f" "$H/bin/"; done
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
check(not [e for e in ev if e["event"] in ("merge_conflict", "post_merge_fail", "dup_task_id", "merge_failed", "deps_deadlock", "stale_claim")],
      "no conflicts, duplicate ids, failed merges, deadlocks or stale claims")
sys.exit(1 if bad else 0)
PY
for id in a b; do
  h=$(python3 -c "import json; r=json.load(open('$M.$id/.agent/tasks.json')); print(sorted(k for k,v in r.items() if v['owner']=='human'))")
  case $h in "['000']"|"['000', '999']") ok "tt: agent $id human tasks $h" ;; *) bad "tt: agent $id human tasks $h" ;; esac
done

# --- scenario 2: two tasks write the same undeclared file at the same time ---
rm -f "$FX"/*.md
task 001-left.md none "src/left/" "Stub-writes: shared.txt"
task 002-right.md none "src/right/" "Stub-writes: shared.txt"
export STUB_SLEEP=8; run_team tc
M=$PR/tc
grep -q '"merge_conflict".*shared.txt' "$M/.agent/team/events.jsonl" && ok "tc: the undeclared shared file was caught as a merge conflict" || bad "tc: no merge conflict recorded for shared.txt"
[ -d "$M/src/left" ] && [ -d "$M/src/right" ] && ok "tc: after the conflict both tasks' work is in main" || bad "tc: work missing in main"
grep -qs 'cannot go into main yet' "$M.a/DECISIONS.md" "$M.b/DECISIONS.md" "$M/DECISIONS.md" && ok "tc: the reopened task was told why (DECISIONS.md)" || bad "tc: no conflict reason in DECISIONS.md"
for id in a b; do grep -h "team:" "$PR/tc.$id/.agent/loop.log" | sed "s/^/     $id /" | tail -6; done
kill $SRV 2>/dev/null
exit $fail
