#!/usr/bin/env bash
# test_team_timing.sh DRIVER_DIR : the test gate and the timing watch (2026-10-08), end to end. Run on the VM (~4 min).
# DRIVER_DIR holds the driver files and pylib/ (default: the repo's harness/driver next to this file).
# Scenario 1 (tg): two stub agents build a pytest project with an ordinary test and a timing test (tests/test_perf.py,
#   slow on purpose, failing while src/slow.txt exists). Task 002 adds src/slow.txt. Expected:
#   - the driver's test runs never include the timing test, and there is about one per merged task (was three);
#   - a baseline is read from the recorded result of the code, not run again;
#   - after 002 is merged the timing test runs in the background, first unlocked, then alone; an agent session starts
#     while it runs (nobody waits for it); task 998 is written into main once, naming the test and 002;
#   - the next agent to finish a task takes 998 (Priority: first); its fix (STUB_ON_998) goes into main, the timing
#     run after that passes, 998 stays done; every task done, the goal check, both loops stop.
# Scenario 2 (tw): timing-watch run directly on a team folder: load noise (fails unlocked, passes alone) opens nothing;
#   an alone run that cannot get the VM to itself is "busy" (no task, tried again); a real failure opens 998 once, a
#   second failure while 998 is open writes nothing; code already tested is not run
#   again; a watch started while another runs leaves the "again" marker and exits. Plus the pytest plugin: an ordinary
#   run leaves the timing test out and says so, naming the file runs it.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DRIVER=${1:-$HERE/../../harness/driver}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
H=$T/home; PR=$H/projects; mkdir -p "$H/bin" "$H/.npm-global/bin" "$H/.agent-kit/agents" "$H/.agent-kit/pylib" "$PR"
for f in agent-loop agent-team-lib agent-team task-audit codemap-gen decisions-archive plan-schedule pitfalls-sync team-takeover team-carve timing-watch; do
  [ -f "$DRIVER/$f" ] && cp "$DRIVER/$f" "$H/bin/"
done
cp "$DRIVER/pylib/agent_testlock.py" "$H/.agent-kit/pylib/"
for f in agent-start agent-stop; do sudo cat "/home/agent/bin/$f" > "$H/bin/$f"; done
cp "$HERE/stubpi-team" "$H/.npm-global/bin/pi"; chmod +x "$H/bin/"* "$H/.npm-global/bin/pi"
sudo cp -r /home/agent/.agent-kit/template "$H/.agent-kit/" && sudo chown -R "$(id -u)" "$H/.agent-kit/template"
printf '[user]\n\tname = t\n\temail = t@t\n' > "$H/.gitconfig"
: > "$H/.agent-kit/agents/a.env"; : > "$H/.agent-kit/agents/b.env"
FX=$T/fixtures; mkdir -p "$FX"
task() {  # task <file> <depends> <touches> [extra line]
  printf 'Status: open\n# %s\n\nDepends on: %s\nTouches: %s\n%s\n## Acceptance criteria\n- [ ] done\n' "${1%.md}" "$2" "$3" "${4:-}" > "$FX/$1"
}
RUNLOG=$T/runs.log; : > "$RUNLOG"
export HOME=$H STUB_FIXTURES=$FX STUB_SLEEP=3 TEAM_WAIT_S=3 NO_PROGRESS_LIMIT=20 RUNLOG PERF_SLEEP=15
export TEST_CMD="GATE=1 python3 -m pytest -q" STUB_ON_998="git rm -q src/slow.txt"
export PATH="$H/.npm-global/bin:$H/bin:/usr/local/bin:/usr/bin:/bin"
MS=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
mkdir -p "$T/srv/v1"; echo '{"data":[{"id":"stub"}]}' > "$T/srv/v1/models"
python3 -m http.server --bind 127.0.0.1 "$MS" --directory "$T/srv" > /dev/null 2>&1 & SRV=$!
export LLM_URL=http://127.0.0.1:$MS
fail=0; ok() { echo "ok   $*"; }; bad() { echo "FAIL $*"; fail=1; }

pytests() {  # pytests <dir> : the test files of the scenarios
  mkdir -p "$1/tests"
  cat > "$1/tests/conftest.py" <<'EOF'
import os, time
def _line(what, n="", perf=""):
    log = os.environ.get("RUNLOG")
    if log:
        kind = "gate" if os.environ.get("GATE") else "watch" if os.environ.get("AGENT_TIMING") == "1" else "other"
        with open(log, "a") as f:
            f.write(f"{time.time():.1f} {what} {kind} pid={os.getpid()} held={os.environ.get('AGENT_TEST_LOCK_HELD', '-')} perf={perf} n={n}\n")
def pytest_collection_finish(session):
    _line("start", len(session.items), sum("perf" in i.nodeid for i in session.items))
def pytest_unconfigure(config):
    _line("end")
EOF
  printf 'def test_app():\n    assert True\n' > "$1/tests/test_app.py"
  cat > "$1/tests/test_perf.py" <<'EOF'
import fcntl, os, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
def alone():   # this run holds tests.lock exclusively (a second open file cannot even share it)
    fd = os.open(os.environ["AGENT_TEST_LOCK"], os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
def test_page_fast():
    time.sleep(float(os.environ.get("PERF_SLEEP", "15")))
    assert not (ROOT / "src/slow.txt").exists(), "src/slow.txt makes the page slow"
    if (ROOT / "src/noisy.txt").exists() and not alone():
        raise AssertionError("slow only while other test runs load the VM")
EOF
}

# --- scenario 1 ---
task 001-a.md none "src/a/"
task 002-slow.md none "src/slow.txt"
task 003-c.md "002" "src/c/"
task 004-d.md none "src/d/" "Stub-sleep: 90"
P=$PR/tg; mkdir -p "$P"; echo "# Test goal" > "$P/GOAL.md"
INIT_BRANCH=main TEAM_PLAN=1 AGENT_LOOP_INIT_ONLY=1 agent-loop "$P" > /dev/null
pytests "$P"; git -C "$P" add tests && git -C "$P" commit -qm "tests"
agent-team init tg a b > /dev/null || bad "tg: init failed"
agent-team start tg > /dev/null
for _ in $(seq 1 120); do
  sleep 5
  [ -f "$P/.agent/team/STOP" ] && ! pgrep -u "$(id -u)" -f "agent-loop $P" > /dev/null && break
done
agent-team status tg | sed 's/^/     /'
[ -f "$P/.agent/team/STOP" ] && ok "tg: goal check ran and wrote STOP" || bad "tg: no STOP"
if pgrep -u "$(id -u)" -f "agent-loop $P" > /dev/null; then bad "tg: loops still running"; agent-team stop tg --now > /dev/null; else ok "tg: both loops stopped"; fi
sleep 2   # a last timing run may still be finishing
nd=$(for f in "$P"/tasks/[0-9]*.md; do head -1 "$f"; done | grep -vc 'Status: done')
[ "$nd" -eq 0 ] && ok "tg: every task done in main ($(ls "$P"/tasks/[0-9]*.md | wc -l) tasks)" || bad "tg: $nd task(s) not done in main"
echo "     runs:"; sed 's/^/       /' "$RUNLOG"
cat "$P"/.agent/team/timing/runs.jsonl 2>/dev/null | sed 's/^/     timing: /'
python3 - "$RUNLOG" "$P" <<'PY' || fail=1
import json, os, re, sys, glob, datetime
runlog, P = sys.argv[1:]
runs = [l.split() for l in open(runlog) if l.strip()]
ev = [json.loads(l) for l in open(f"{P}/.agent/team/events.jsonl") if l.strip()]
bad = 0
def check(cond, msg):
    global bad
    print(("ok   " if cond else "FAIL ") + "tg: " + msg); bad += not cond
gate = [r for r in runs if r[1] == "start" and r[2] == "gate"]
watch = [r for r in runs if r[1] == "start" and r[2] == "watch"]
merges = [e for e in ev if e["event"] == "merge"]
check(gate and all(r[5] == "perf=0" for r in gate), f"no driver test run included the timing test ({len(gate)} runs)")
check(len(gate) <= len(merges) + 4, f"driver test runs about one per merge: {len(gate)} runs, {len(merges)} merges (the old gate: three per task)")
logs = "".join(open(f).read() for f in glob.glob(f"{P}.*/.agent/loop.log"))
rec = len(re.findall(r"baseline for \S+: .*recorded for this code", logs))
check(rec >= 1, f"baselines read from the recorded results: {rec} of {len(re.findall(r'baseline for ', logs))}")
unlocked = [r for r in watch if r[4] == "held=1"]
alone = [r for r in watch if r[4] == "held=-"]
check(watch and all(r[5] != "perf=0" for r in watch), f"the watch ran the timing test ({len(watch)} runs)")
check(len(alone) >= 1 and len(unlocked) >= 1, f"a failing unlocked run was run again alone ({len(unlocked)} unlocked, {len(alone)} alone)")
# nobody waits for the watch: the agents' merges (driver test runs) go on while a watch run is in progress
spans, open_ = [], {}
for r in runs:
    if r[2] == "watch":
        if r[1] == "start": open_[r[3]] = float(r[0])
        elif r[3] in open_: spans.append((open_.pop(r[3]), float(r[0])))
during = [r for r in gate if any(a < float(r[0]) < b for a, b in spans)]
check(len(during) >= 1, f"the agents went on merging while the timing test ran ({len(during)} driver test runs during watch runs)")
fails = [e for e in ev if e["event"] == "timing_fail"]
check(len(fails) >= 1, f"timing failure recorded ({len(fails)} timing_fail events)")
t998 = open(f"{P}/tasks/998-timing-check.md").read() if os.path.exists(f"{P}/tasks/998-timing-check.md") else ""
check("tests/test_perf.py::test_page_fast" in t998 and "002" in t998 and "Priority: first" in t998, "998 names the failing test, the 002 merge, Priority: first")
opened = os.popen(f"git -C {P} log --oneline main -- tasks/998-timing-check.md").read()
check(opened.count("timing check") == 1, f"998 written into main once ({opened.count('timing check')})")
i = next((n for n, e in enumerate(ev) if e["event"] == "timing_fail"), None)
nxt = next((e for e in ev[i + 1:] if e["event"] == "claim"), None) if i is not None else None
check(nxt is not None and "/998-" in nxt["task"], f"the next claim after the failure was 998 ({nxt and nxt['task']})")
st = json.load(open(f"{P}/.agent/team/timing/state.json"))
check(st.get("result") == "pass" and t998.startswith("Status: done"), f"timing passes on main after the fix, 998 done (last result {st.get('result')})")
sys.exit(1 if bad else 0)
PY
agent-team stop tg --now > /dev/null 2>&1

# --- scenario 2 ---
W=$PR/tw; mkdir -p "$W/src" "$W/.agent/team"; : > "$W/.agent/team/events.jsonl"
git -C "$W" init -q -b main; printf '.agent/\n' > "$W/.gitignore"; pytests "$W"; echo x > "$W/src/app.txt"
printf '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n' > "$W/pyproject.toml"
git -C "$W" add -A && git -C "$W" commit -qm init
export AGENT_TEST_LOCK=$W/.agent/team/tests.lock PYTHONPATH=$H/.agent-kit/pylib PYTEST_PLUGINS=agent_testlock PERF_SLEEP=1
unset TEST_CMD
out=$(cd "$W" && python3 -m pytest -q 2>&1)
grep -q "1 timing test(s) (test files named \*perf\*) left out" <<<"$out" && grep -q "1 passed" <<<"$out" \
  && ok "tw: an ordinary run leaves the timing test out and says so" || bad "tw: ordinary run: $out"
out=$(cd "$W" && python3 -m pytest -q tests/test_perf.py 2>&1)
grep -q "1 passed" <<<"$out" && ok "tw: naming the file runs the timing test" || bad "tw: named run: $out"
tw() { timing-watch "$W" "$W"; }
nruns() { wc -l < "$W/.agent/team/timing/runs.jsonl" 2>/dev/null || echo 0; }
last() { tail -1 "$W/.agent/team/timing/runs.jsonl" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"])'; }
tw; [ "$(last)" = pass ] && ok "tw: green main: pass" || bad "tw: green main: $(last)"
echo n > "$W/src/noisy.txt"; git -C "$W" add -A && git -C "$W" commit -qm noisy; tw
[ "$(last)" = noise ] && [ ! -f "$W/tasks/998-timing-check.md" ] && ok "tw: fails unlocked, passes alone: noise, no task" || bad "tw: noise case: $(last)"
git -C "$W" commit -q --allow-empty -m "notes only"; echo note >> "$W/PROGRESS.md"; git -C "$W" add -A && git -C "$W" commit -qm notes
n=$(nruns); tw; [ "$(nruns)" = "$n" ] && ok "tw: same code (notes changed only): not run again" || bad "tw: same code ran again"
git -C "$W" rm -q src/noisy.txt; echo s > "$W/src/slow.txt"; git -C "$W" add -A && git -C "$W" commit -qm "slow down"
python3 -c 'import fcntl,os,sys,time; fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT); fcntl.flock(fd,fcntl.LOCK_SH); time.sleep(6)' "$AGENT_TEST_LOCK" & LP=$!
sleep 0.5; TIMING_ALONE_WAIT=2 tw; wait $LP
[ "$(last)" = busy ] && [ ! -f "$W/tasks/998-timing-check.md" ] && ok "tw: the alone run never got the VM to itself: busy, no task" || bad "tw: busy case: $(last)"
tw
[ "$(last)" = fail ] && grep -q '^Status: open' "$W/tasks/998-timing-check.md" && grep -q 'slow down' "$W/tasks/998-timing-check.md" \
  && ok "tw: real failure: 998 opened, naming the commit" || bad "tw: real failure: $(last)"
echo y >> "$W/src/app.txt"; git -C "$W" add -A && git -C "$W" commit -qm "more"; tw
c=$(git -C "$W" log --oneline -- tasks/998-timing-check.md | grep -c 'timing check')
[ "$(last)" = fail ] && [ "$c" = 1 ] && ok "tw: failing again while 998 is open: nothing written" || bad "tw: 998 written $c times"
python3 -c 'import fcntl,os,sys,time; fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT); fcntl.flock(fd,fcntl.LOCK_EX); time.sleep(4)' "$W/.agent/team/timing/run.lock" & LP=$!
sleep 1; s=$(date +%s); tw; e=$(date +%s); wait $LP
[ -f "$W/.agent/team/timing/again" ] && [ $((e - s)) -le 1 ] && ok "tw: a second watch leaves 'again' and exits" || bad "tw: coalescing"
kill $SRV 2>/dev/null
[ $fail -eq 0 ] && echo "ALL OK" || echo "SOME FAILED"
exit $fail
