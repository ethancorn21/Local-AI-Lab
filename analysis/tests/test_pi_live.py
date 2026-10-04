"""End-to-end test of pi-live --follow in a pseudo-terminal, screen and scrollback recorded by pyte.

Feeds a real session file into a fake project the way the driver and Pi write it (big lines split mid-write), adds
loop-log lines, ends the session, starts a second one, resizes, toggles thinking, stops the loop, quits. Checks:
footer rows only ever at the bottom and never in scrollback, every tool header of the replay present and in order,
session headers in order, loop lines shown, activity row states, footer removed on exit.

Usage: python test_pi_live.py <session.jsonl>   any real .agent/sessions/iter-*.jsonl (a few MB); needs pyte
(pip install pyte pygments). Work files go to a temporary directory."""
import fcntl, json, os, pty, re, signal, struct, subprocess, sys, termios, threading, time
import pyte

import tempfile
S = tempfile.mkdtemp(prefix="pi-live-e2e-")
PI_LIVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../harness/tools/pi-live")
PY = sys.executable
PROJ = os.path.join(S, "proj")
SESS = open(sys.argv[1]).read().splitlines(keepends=True)
ROWS, COLS = 40, 120

subprocess.run(["rm", "-rf", PROJ])
os.makedirs(PROJ + "/.agent/sessions")
log = open(PROJ + "/.agent/loop.log", "a", buffering=1)
def logl(msg): log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
def pidfile(name, proc): open(f"{PROJ}/.agent/{name}", "w").write(str(proc.pid))
loop_proc = subprocess.Popen(["sleep", "1000"]); pidfile("loop.pid", loop_proc)
pi_proc = subprocess.Popen(["sleep", "1000"]); pidfile("pi.pid", pi_proc)
logl("iteration 1: tasks/000-plan.md (harness abc)")
f1 = open(PROJ + "/.agent/sessions/iter-0001.jsonl", "w", buffering=1)
f1.writelines(SESS[:1500]); f1.flush()

screen = pyte.HistoryScreen(COLS, ROWS, history=200000, ratio=0.001)
stream = pyte.ByteStream(screen)
lock = threading.Lock()

pid, fd = pty.fork()
if pid == 0:
    fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    os.environ.update(TERM="xterm-256color", LLM_URL="http://127.0.0.1:9")
    os.execv(PY, [PY, PI_LIVE, "--follow", PROJ])

raw = bytearray()
def reader():
    while True:
        try:
            b = os.read(fd, 65536)
        except OSError:
            return
        if not b:
            return
        with lock:
            raw.extend(b)
            stream.feed(b)
threading.Thread(target=reader, daemon=True).start()

def line_text(line, cols):
    return "".join(line[x].data for x in range(cols)).rstrip()
def snapshot():
    with lock:
        hist = [line_text(l, screen.columns) for l in screen.history.top]
        disp = [l.rstrip() for l in screen.display]
        return hist, disp, screen.lines, screen.columns
FAIL = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)
def footer_ok(tag, activity_re=None):
    hist, disp, rows, cols = snapshot()
    used = [i for i, l in enumerate(disp) if l]
    last = used[-1] if used else -1
    status_rows = [i for i, l in enumerate(disp) if l.startswith(" session 1 |") or l.startswith(" session 2 |")]
    hint_rows = [i for i, l in enumerate(disp) if "› message the agent" in l]
    check(len(status_rows) == 1 and status_rows[0] == last, f"{tag}: one status bar, last used row ({status_rows}, last {last})")
    check(len(hint_rows) == 1 and hint_rows[0] == last - 1, f"{tag}: one message line, just above it ({hint_rows})")
    leaked = [l for l in hist if "› message the agent" in l or re.match(r"^ session \d+ \|", l)]
    check(not leaked, f"{tag}: no footer rows in scrollback ({len(leaked)})")
    if activity_re:
        act = disp[last - 2] if last >= 2 else ""
        check(re.search(activity_re, act) is not None, f"{tag}: activity row ~ /{activity_re}/: {act[:90]!r}")
    return hist, disp

def feed(f, lines, rate=4000):
    for i, l in enumerate(lines):
        if len(l) > 8192:   # a big event written in two parts, as a long write lands page by page
            f.write(l[: len(l) // 2]); f.flush(); time.sleep(0.03)
            f.write(l[len(l) // 2:]); f.flush()
        else:
            f.write(l)
        if i % 200 == 0:
            f.flush(); time.sleep(200 / rate)
    f.flush()

time.sleep(2.0)
footer_ok("after replay")
# live: a thinking block streams; Ctrl-T hides then shows thinking mid-stream
mid = len(SESS) // 3
feed(f1, SESS[1500:mid])
logl("WARNING: DECISIONS.md is 94 KB, over Pi's 50 KB single-read limit")
os.write(fd, b"\x14"); time.sleep(0.3); os.write(fd, b"\x14")
feed(f1, SESS[mid:])
time.sleep(1.0)
footer_ok("end of session 1")
# between sessions
pi_proc.kill(); os.remove(PROJ + "/.agent/pi.pid")
logl("no agent commit (4/5)")
logl("decisions archive synced (after iteration 1)")
time.sleep(2.0)
footer_ok("between sessions", r"between sessions · decisions archive synced")
# session 2, with a resize in the middle (taller and wider: pyte does not re-wrap, so no narrowing here)
pi_proc = subprocess.Popen(["sleep", "1000"]); pidfile("pi.pid", pi_proc)
logl("iteration 2: tasks/221-e2e-explore-theme.md (harness abc)")
f2 = open(PROJ + "/.agent/sessions/iter-0002.jsonl", "w", buffering=1)
feed(f2, SESS[:3000])
with lock:
    screen.resize(46, 150)
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 46, 150, 0, 0))
time.sleep(1.5)
feed(f2, SESS[3000:6000])
time.sleep(1.0)
footer_ok("after resize")
# typing shows on the message line
os.write(fd, b"hello agent"); time.sleep(0.5)
hist, disp, rows, cols = snapshot()
check(any(l.startswith("› hello agent") for l in disp), "typed text on the message line")
os.write(fd, b"\x15"); time.sleep(0.3)
# loop stopped
loop_proc.kill(); loop_proc.wait(); pi_proc.kill(); os.remove(PROJ + "/.agent/pi.pid")
time.sleep(2.0)
footer_ok("loop stopped", r"■ loop stopped")

# transcript checks
hist, disp, rows, cols = snapshot()
text = "\n".join(hist + disp)
heads = [m.start() for m in re.finditer(r"── session \d", text)]
check([text[h:h + 12] for h in heads] == ["── session 1", "── session 2"], f"session headers in order: {[text[h:h+12] for h in heads]}")
check("◆" in text and "WARNING: DECISIONS.md is 94 KB" in text and "no agent commit (4/5)" in text, "loop lines shown")
check(not re.search(r"◆ \d\d:\d\d:\d\d iteration \d+:", text), "iteration lines left to the session header")
check("(thinking hidden)" in text and "(thinking shown)" in text, "thinking toggle notes shown")
# every tool header of session 1, in order (same as a plain replay of the same events)
plain = subprocess.run([PY, PI_LIVE], input="".join(SESS), capture_output=True, text=True).stdout
want = [l.strip() for l in plain.splitlines() if l.startswith("⏺ ") and "(" in l]
s1 = text[heads[0]:heads[1]]
flat = re.sub(r"\s+", "", s1)
pos, missing = 0, []
for h in want:
    i = flat.find(re.sub(r"\s+", "", h), pos)
    if i < 0:
        missing.append(h)
    else:
        pos = i
check(not missing, f"session 1: all {len(want)} tool headers present and in order (missing {missing[:3]})")
# thinking text of session 1 complete (whitespace-insensitive), ignoring the toggled stretch
thinks, cur = [], ""
for l in SESS:
    e = json.loads(l)
    d = (e.get("assistantMessageEvent") or {})
    if d.get("type") == "thinking_delta":
        cur += d.get("delta", "")
    elif d.get("type") == "thinking_end":
        thinks.append(cur); cur = ""
ok = sum(1 for t in thinks if re.sub(r"\s+", "", t)[:400] in flat)
check(ok >= len(thinks) - 1, f"session 1: thinking blocks shown {ok}/{len(thinks)} (one may be cut by the toggle)")
# quit
os.write(fd, b"\x03"); time.sleep(1.0)
try:
    wpid, st = os.waitpid(pid, os.WNOHANG)
except ChildProcessError:
    wpid = pid
check(wpid == pid, "Ctrl-C ends the viewer")
hist, disp, rows, cols = snapshot()
check(not any("› message the agent" in l or l.startswith(" session ") for l in disp), "footer removed on exit")
check(raw.rfind(b"\x1b[?25h") > raw.rfind(b"\x1b[?25l"), "cursor shown again on exit")
open(os.path.join(S, "e2e-screen.txt"), "w").write("\n".join(hist[-60:] + ["=== display ==="] + disp))
print("FAILED:" if FAIL else "ALL PASS", len(FAIL))
sys.exit(1 if FAIL else 0)
