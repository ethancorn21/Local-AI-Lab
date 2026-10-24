#!/usr/bin/env python3
"""lcd: the local-compute service's daemon (docs/local-compute.md).

Callers name a setup and hand over work; lcd queues it, keeps one setup loaded on the two RTX 3090s, switches when
the queue asks for another, and runs each job there. Why (2026-10-17): the agent harness was scrapped for one service
that Claude (through MCP), the GrokBots (through HTTP) and scripts call with "setup X, do this"; the setups and their
settings are the serving bench's decision (docs/serving-bench.md).

  setups  main   production's 4-bit Qwen3.8-27B across both 3090s: 8 agents at 128k. chat, implement, verify jobs
          swarm  Qwen3.6-35B-A3B 4-bit across both 3090s, 64k a scout: swarm jobs (and chat)
          big    Flash-Next IQ3_S on Strata: one request at a time, 262k, reads long prompts 4x faster than main
  HTTP    POST /v1/chat/completions   OpenAI-compatible; "model" names the setup (anything else means main)
          GET  /v1/models
          POST /uploads               body: a .tar.gz of the files a swarm or implement job works on -> {"upload"}
          POST /jobs                  {"kind": "chat"|"swarm"|"implement", ...} -> the job
          GET  /jobs                  your jobs, newest first
          GET  /jobs/<id>[?wait=N]    one of your jobs, with its result once done; wait: up to N s for its end
          POST /jobs/<id>/cancel
          POST /jobs/<id>/approve     admin: start a job that waits for the owner's approval
          GET  /status                what is loaded, what waits
          POST /admin/pause [{"stop": true}] | /admin/resume | /admin/load {"setup": ...}
          GET  /health                no token
  CLI     lcd.py serve
          lcd.py token add <name> [--admin] [--approve swarm,implement] [--scopes chat,swarm,implement]
          lcd.py token list | token remove <name>

Every request but /health carries "Authorization: Bearer <token>"; the tokens are stored hashed. A caller sees only
its own jobs, and a caller whose token lists a kind under "approve" (GrokBot's agent jobs, the owner's rule of
2026-10-17) gets that kind of job held until an admin approves it, with a ping to the owner's phone. Prompts and
files are kept only while a job needs them, results for RESULT_DAYS; the request log holds no content.
"""
import argparse, contextlib, difflib, fnmatch, hashlib, http.server, json, os, re, secrets, shutil, sqlite3
import subprocess, sys, tarfile, threading, time, traceback, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "bench")]
import scout   # noqa: E402  (the swarm's agent loop, server/bench/scout.py, deployed beside this file)

STATE = os.environ.get("LC_STATE", "/var/lib/localcompute")
TOKENS = os.environ.get("LC_TOKENS", "/etc/localcompute/tokens.json")
SETUP_CMD = os.environ.get("LC_SETUP", os.path.join(HERE, "lc-setup"))
PORTS = [int(p) for p in os.environ.get("LC_PORTS", "8080,8081").split(",")]   # the two ports the tailnet forwards
IDLE_SETUP = os.environ.get("LC_IDLE_SETUP", "main")   # loaded when nothing asks for another; "" leaves the cards empty
MAX_WAIT = int(os.environ.get("LC_MAX_WAIT", "600"))   # s: another setup's work waits this long, then the loaded
# setup stops taking new work, finishes what it runs, and the service switches (a switch costs 1-3.5 min)
IDLE_RETURN = int(os.environ.get("LC_IDLE_RETURN", "900"))   # s idle on swarm or big before main comes back
CHECK_EVERY = int(os.environ.get("LC_CHECK_EVERY", "30"))   # s between looks at the containers and the bench
PING_CMD = os.environ.get("LC_PING")   # tests: a command that takes the phone message on stdin
SANDBOX_SCOPE = os.environ.get("LC_SANDBOX_SCOPE", "1") == "1"   # tests on a laptop: no systemd scope
SETUPS = {"main": {"port": 8101, "seats": 8}, "swarm": {"port": 8102, "seats": 1}, "big": {"port": 8103, "seats": 1}}
for _kv in filter(None, os.environ.get("LC_SETUP_PORTS", "").split(",")):   # tests: main=18101,...
    SETUPS[_kv.split("=")[0]]["port"] = int(_kv.split("=")[1])
KINDS = ("chat", "swarm", "implement")
TERMINAL = ("done", "failed", "cancelled")
UPLOAD_MAX = 64 << 20          # bytes of .tar.gz
UNPACKED_MAX = 512 << 20       # bytes once unpacked
BODY_MAX = 32 << 20            # a chat request or job description
JOBS_PER_CALLER = 20           # queued or held at once
RESULT_DAYS = 7
REQUEST_WAIT = 1800            # s a chat request waits for its setup before a 503

# A swarm scout reads ~440 lines of an area (the bench's 100 areas over 44k lines: about 42 of 60 planted bugs in 30
# minutes on the 4-bit A3B; 200 smaller areas found one or two more for two thirds more noise)
AREA_LINES = 440
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache"}
BUGHUNT = (
    "You are one scout in a swarm reviewing a codebase for bugs. Other scouts cover the rest; cover YOUR AREA "
    "thoroughly:\n{area}\n\nRead your area with read_file (line ranges). You may read or search other files to see how "
    "something is used. For each likely bug - a wrong comparison or boundary, an inverted condition, a wrong variable, "
    "argument or index, a min/max or and/or mix-up, a wrong return value - call report(path, line, what is wrong). "
    "Report real bugs only, not style or missing features, and give the exact line. When your area is covered, call "
    "finish with a one-line summary.")
CUSTOM = (
    "You are one scout in a swarm reading a codebase. Other scouts cover the rest; cover YOUR AREA thoroughly:\n{area}"
    "\n\nWhat to look for: {brief}\n\nRead your area with read_file (line ranges). You may read or search other files to "
    "see how something is used. For each place that matches what to look for, call report(path, line, what you "
    "found), with the exact line. When your area is covered, call finish with a one-line summary.")
scout.BRIEFS["lc-bughunt"] = BUGHUNT
SWARM_CANCEL = threading.Event()   # one swarm runs at a time (the swarm setup has one seat)
_scout_chat = scout.chat


def _chat_or_stop(*a, **kw):   # a cancelled swarm's scouts stop at their next turn (scout ends on any exception)
    if SWARM_CANCEL.is_set():
        raise RuntimeError("cancelled")
    return _scout_chat(*a, **kw)


scout.chat = _chat_or_stop

# The implementation agent (the "slim worker" of 2026-10-17): the local model as a tight implementation agent working
# from a spec that Claude or a person wrote, checked by tests it may not edit. The bench's best use of the 27B was to
# retry against the tests until an attempt passes (12 of 14 held-out tasks); run_tests in the loop is that retry.
IMPL_TOOLS = scout.TOOLS[:3] + [
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a file with the full content given.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file",
     "description": "Replace one exact piece of text in a file. old must appear exactly once: include enough lines around it.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                                     "new": {"type": "string"}}, "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {"name": "run_tests",
     "description": "Run the project's test command in a sandbox (no network) and see the end of its output.",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "finish", "description": "End: the tests pass and the spec is done.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]
PROTECT = ["tests/*", "*/tests/*", "test/*", "*/test/*", "test_*.py", "*/test_*.py", "*_test.py", "conftest.py",
           "*/conftest.py", "*.test.*", "*.spec.*"]
IMPL_CAP, IMPL_RESERVE, IMPL_THINK, IMPL_ANSWER = 131072, 14000, 4096, 8192
VERIFY_BUG = ("A scout reviewing this code for bugs reported the finding below. Decide whether it is a real bug: code "
              "that does the wrong thing when it runs. Style, naming, missing features, missing error handling and "
              "guesses about intent are not bugs.\n\n{path}, lines {x}-{y}, the reported line marked >>:\n\n{code}\n\n"
              "The report, at line {line}: {finding}\n\nAnswer with BUG or NOT A BUG on the first line, then one sentence why.")
VERIFY_CUSTOM = ("A scout was asked to find this in a codebase: {brief}\nIt reported the finding below. Decide whether "
                 "the report is right about this code.\n\n{path}, lines {x}-{y}, the reported line marked >>:\n\n{code}\n\n"
                 "The report, at line {line}: {finding}\n\nAnswer with YES or NO on the first line, then one sentence why.")


def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def ping(text):
    """the owner's phone, through the doorbell relay (as the bench watchdog and the thermal guard do)"""
    try:
        cmd = PING_CMD.split() if PING_CMD else ["sudo", "-n", "runuser", "-u", "doorbell", "--", "env",
                                                 "SSH_ORIGINAL_COMMAND=send", "/usr/local/bin/doorbell"]
        subprocess.run(cmd, input=json.dumps({"kind": "info", "text": text}), text=True, capture_output=True, timeout=60)
    except Exception as e:
        log(f"ping failed: {e}")


def post(url, body, timeout=3600):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class JobError(Exception):
    pass


# ---- tokens ----

def token_hash(t):
    return hashlib.sha256(t.encode()).hexdigest()


def load_tokens():
    try:
        return json.load(open(TOKENS))["tokens"]
    except FileNotFoundError:
        return []


def save_tokens(toks):
    os.makedirs(os.path.dirname(TOKENS), exist_ok=True)
    with open(TOKENS + ".new", "w") as f:
        json.dump({"tokens": toks}, f, indent=1)
    os.chmod(TOKENS + ".new", 0o600); os.replace(TOKENS + ".new", TOKENS)


# ---- jobs ----

class Jobs:
    """The queue: one row per job in sqlite; a job's files (params, result, log, workspace) under STATE/jobs/<id>."""
    COLS = ("id", "caller", "kind", "setup", "state", "created", "started", "finished", "summary", "error", "parent",
            "progress", "seats")

    def __init__(self, path):
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.c.execute("pragma journal_mode=wal")
        self.c.execute(f"create table if not exists jobs ({', '.join(self.COLS)})")
        self.lock = threading.Lock()

    def add(self, **kw):
        row = {k: kw.get(k) for k in self.COLS}
        with self.lock:
            self.c.execute(f"insert into jobs values ({', '.join('?' * len(self.COLS))})", [row[k] for k in self.COLS])
        return row

    def set(self, jid, **kw):
        with self.lock:
            self.c.execute(f"update jobs set {', '.join(f'{k} = ?' for k in kw)} where id = ?", [*kw.values(), jid])

    def get(self, jid):
        with self.lock:
            r = self.c.execute(f"select {', '.join(self.COLS)} from jobs where id = ?", (jid,)).fetchone()
        return dict(zip(self.COLS, r)) if r else None

    def find(self, caller=None, states=None, limit=200):
        q, args = f"select {', '.join(self.COLS)} from jobs where 1", []
        if caller:
            q += " and caller = ?"; args.append(caller)
        if states:
            q += f" and state in ({', '.join('?' * len(states))})"; args += list(states)
        with self.lock:
            rows = self.c.execute(q + " order by created desc limit ?", [*args, limit]).fetchall()
        return [dict(zip(self.COLS, r)) for r in rows]


def jdir(jid):
    return os.path.join(STATE, "jobs", jid)


def unpack(upload, dest):
    """an upload into dest: regular files and folders only, nothing outside dest (tarfile's data filter)"""
    src = os.path.join(STATE, "uploads", f"{upload}.tar.gz")
    if not re.fullmatch(r"[0-9a-f]{24}", upload or "") or not os.path.exists(src):
        raise JobError(f"no upload {upload}")
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(src, "r:gz") as t:
        t.extractall(dest, members=[m for m in t.getmembers() if m.isfile() or m.isdir()], filter="data")


def text_files(root, globs):
    """[(relative path, lines)] of the text files under root whose names match one of globs"""
    out = []
    for r, ds, fs in os.walk(root):
        ds[:] = sorted(d for d in ds if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(fs):
            if not any(fnmatch.fnmatch(f, g) for g in globs):
                continue
            full = os.path.join(r, f)
            try:
                if os.path.getsize(full) > 2 << 20 or b"\0" in open(full, "rb").read(8192):
                    continue
                out.append((os.path.relpath(full, root), sum(1 for _ in open(full, encoding="utf-8", errors="replace"))))
            except OSError:
                continue
    return out


def split_areas(files, n=None):
    """scout.split's slicing: the files cut into n areas of about equal lines, a long file by line range"""
    total = sum(k for _, k in files)
    n = n or max(1, min(100, round(total / AREA_LINES)))
    per = max(1, -(-total // n))
    areas, cur, room = [], [], per
    for f, k in files:
        start = 1
        while k - start + 1 > 0:
            take = min(room, k - start + 1)
            cur.append(f"{f} lines {start}-{start + take - 1}"); start += take; room -= take
            if room == 0:
                areas.append(cur); cur, room = [], per
    if cur:
        areas.append(cur)
    return [{"id": f"s{i:03d}", "area": a} for i, a in enumerate(areas)], total


def pool_fit(url, n):
    """as many scouts at once as 85% of the server's KV pool holds at 40k each (bench-phase.sh's pool_fit: with all
    100 at once every model spent its time reading conversations again, 4.5x slower and fewer finds)"""
    try:
        with urllib.request.urlopen(url.rsplit("/v1", 1)[0] + "/metrics", timeout=10) as r:
            m = re.findall(r'^vllm:cache_config_info\{[^}]*kv_cache_size_tokens="([0-9.]+)"', r.read().decode(), re.M)
        pool = sum(int(float(v)) for v in m)
    except Exception:
        pool = 0
    return min(n, max(2, int(0.85 * pool / 40000))) if pool else min(n, 16)


def cluster(scouts):
    """the scouts' reports, those within 3 lines of each other in one file as one finding, most-reported first"""
    reps = sorted((f["path"], f["line"], f["finding"], sc["id"]) for sc in scouts for f in sc["findings"])
    groups = []
    for p, ln, text, sid in reps:
        g = groups[-1] if groups else None
        if g and g["path"] == p and ln - g["last"] <= 3:
            g["last"] = ln; g["lines"].add(ln); g["texts"].append(text); g["scouts"].add(sid)
        else:
            groups.append({"path": p, "last": ln, "lines": {ln}, "texts": [text], "scouts": {sid}})
    out = [{"path": g["path"], "line": min(g["lines"]), "lines": sorted(g["lines"]), "finding": g["texts"][0],
            "also": g["texts"][1:3], "scouts": len(g["scouts"])} for g in groups]
    return sorted(out, key=lambda x: (-x["scouts"], x["path"], x["line"]))


def sandbox(ws, cmd, timeout):
    """cmd in ws with no network, no view of the box beyond /usr, its own /tmp, in a cgroup of at most 512 tasks
    and 8 GB. Why: the implementation agent's tests run code a model wrote."""
    passwd = os.path.join(STATE, "sandbox-passwd")
    if not os.path.exists(passwd):
        open(passwd, "w").write(f"sandbox:x:{os.getuid()}:{os.getgid()}::/tmp:/bin/bash\n")
    inner = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
             "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
             "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
             "--ro-bind-try", "/etc/alternatives", "/etc/alternatives", "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
             "--ro-bind-try", "/etc/localtime", "/etc/localtime", "--ro-bind", passwd, "/etc/passwd"]
    for d in sorted(os.listdir("/etc")):
        if d.startswith("python3"):
            inner += ["--ro-bind-try", f"/etc/{d}", f"/etc/{d}"]
    inner += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--bind", ws, "/work", "--chdir", "/work",
              "--setenv", "HOME", "/tmp", "--setenv", "USER", "sandbox", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
              "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "bash", "-c", cmd]
    full = ["timeout", "-k", "5", str(timeout)] + inner
    if SANDBOX_SCOPE:
        full = ["sudo", "-n", "systemd-run", "--quiet", "--scope", "--collect", "-p", "TasksMax=512", "-p", "MemoryMax=8G",
                f"--uid={os.getuid()}", f"--gid={os.getgid()}", "--"] + full
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout + 60)
        out, code = (r.stdout + r.stderr), r.returncode
    except subprocess.TimeoutExpired:
        out, code = "", 124
    if code == 124:
        out += f"\n[timed out after {timeout} s]"
    return code, out


def tree_diff(base, ws, paths):
    """a unified diff (a/ b/ prefixes, git apply and patch -p1 take it) of the given files between base and ws"""
    chunks = []
    for p in sorted(paths):
        a, b = os.path.join(base, p), os.path.join(ws, p)
        try:
            old = open(a, encoding="utf-8").read().splitlines(True) if os.path.exists(a) else None
            new = open(b, encoding="utf-8").read().splitlines(True) if os.path.exists(b) else None
        except UnicodeDecodeError:
            continue
        if old == new:
            continue
        d = list(difflib.unified_diff(old or [], new or [], "/dev/null" if old is None else f"a/{p}",
                                      "/dev/null" if new is None else f"b/{p}"))
        for i, line in enumerate(d):   # a last line without a newline, as diff writes it
            if not line.endswith("\n"):
                d[i] = line + "\n\\ No newline at end of file\n"
        chunks.append("".join(d))
    return "".join(chunks)


# ---- the service ----

class Service:
    def __init__(self):
        os.makedirs(os.path.join(STATE, "jobs"), exist_ok=True); os.makedirs(os.path.join(STATE, "uploads"), exist_ok=True)
        self.jobs = Jobs(os.path.join(STATE, "jobs.db"))
        self.cv = threading.Condition()
        self.loaded = None          # the setup whose server answers
        self.switching = None       # the setup being loaded
        self.held = None            # why nothing loads until an admin resumes
        self.paused = False
        self.draining = False       # the loaded setup takes no new work: another setup's work waited MAX_WAIT
        self.want = None            # an admin's load
        self.gpu_busy = False       # the bench or the old production servers hold the 3090s
        self.inflight = {s: 0 for s in SETUPS}   # running jobs and open chat requests
        self.seats = {s: 0 for s in SETUPS}      # seats the running jobs take
        self.waiting = []           # chat requests waiting for their setup: [{"setup", "t"}]
        self.cancel = set()
        self.switches = []
        self.last_busy = time.time()
        self.last_check = 0.0
        for j in self.jobs.find(states=("running", "waiting")):
            self.jobs.set(j["id"], state="failed", error="the service restarted while it ran", finished=time.time())
        running = self.sh("running").stdout.strip()
        if running in SETUPS and self.answers(running):
            self.loaded = running
            log(f"adopted the running {running} server")

    # facts from outside

    def sh(self, *args, timeout=60):
        return subprocess.run([SETUP_CMD, *args], capture_output=True, text=True, timeout=timeout)

    def answers(self, setup):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{SETUPS[setup]['port']}/health", timeout=5).read()
            return True
        except Exception:
            return False

    def url(self, setup):
        return f"http://127.0.0.1:{SETUPS[setup]['port']}/v1"

    # the scheduler

    def admissible(self, setup):
        return (self.loaded == setup and not self.switching and not self.draining and not self.paused
                and not self.held)

    def demand(self):
        """{setup: when its oldest waiting work arrived}"""
        d = {}
        for j in self.jobs.find(states=("queued",), limit=1000):
            d[j["setup"]] = min(d.get(j["setup"], j["created"]), j["created"])
        for w in self.waiting:
            d[w["setup"]] = min(d.get(w["setup"], w["t"]), w["t"])
        return d

    def loop(self):
        while True:
            try:
                self.tick()
            except Exception:
                log("scheduler: " + traceback.format_exc())
            with self.cv:
                self.cv.wait(2)

    def tick(self):
        now = time.time()
        if now - self.last_check >= CHECK_EVERY:
            self.last_check = now
            busy = self.sh("gpu-busy").returncode == 0
            running = self.sh("running").stdout.strip() or None
            lost = None
            with self.cv:
                self.gpu_busy = busy
                if self.loaded and running != self.loaded and not self.switching:
                    lost, self.loaded = self.loaded, None
                    self.held = (f"the {lost} server stopped without the service stopping it (the thermal guard, or a "
                                 "crash); nothing loads until an admin resumes")
                    self.cv.notify_all()
            if lost:
                log(self.held); ping(f"local-compute: {self.held}. Resume: lc resume")
        with self.cv:
            self.start_jobs()
            target = self.decide(now)
        if target:
            self.switch(target)

    def decide(self, now):
        if self.held or self.paused or self.switching or self.gpu_busy:
            return None
        own, dem = self.loaded, self.demand()
        if self.want == own:
            self.want = None
        if self.want and (own is None or self.inflight[own] == 0):
            return self.want
        others = {s: t for s, t in dem.items() if s != own}
        if own is None:
            return min(others, key=others.get) if others else (IDLE_SETUP or None)
        if others:
            oldest = min(others, key=others.get)
            self.draining = now - others[oldest] > MAX_WAIT
            if self.inflight[own] == 0 and (own not in dem or self.draining):
                return oldest
            return None
        self.draining = False
        if (IDLE_SETUP and own != IDLE_SETUP and self.inflight[own] == 0 and own not in dem
                and now - self.last_busy > IDLE_RETURN):
            return IDLE_SETUP
        return None

    def switch(self, target):
        with self.cv:
            prev, self.switching, self.loaded, self.draining = self.loaded, target, None, False
            self.cv.notify_all()
        log(f"switching {prev or 'nothing'} -> {target}")
        t0 = time.time()
        try:
            r = self.sh("start", target, str(SETUPS[target]["port"]), timeout=2400)
            ok, why = r.returncode == 0, (r.stderr or r.stdout).strip()[-400:]
        except subprocess.TimeoutExpired:
            ok, why = False, "no answer in 40 minutes"
        with self.cv:
            self.switching = None
            self.switches = (self.switches + [{"at": round(t0), "from": prev, "to": target, "s": round(time.time() - t0),
                                               "ok": ok}])[-20:]
            if ok:
                self.loaded, self.last_busy = target, time.time()
            else:
                self.held = f"{target} did not start: {why}"
            self.cv.notify_all()
        log(f"{target} {'up' if ok else 'FAILED'} after {time.time() - t0:.0f} s")
        if not ok:
            ping(f"local-compute: {self.held[:300]}. Resume: lc resume")

    def start_jobs(self):
        s = self.loaded
        if not s or not self.admissible(s):
            return
        for j in sorted(self.jobs.find(states=("queued",), limit=1000), key=lambda j: j["created"]):
            if j["setup"] != s:
                continue
            need = j["seats"] or 1
            if self.seats[s] and self.seats[s] + need > SETUPS[s]["seats"]:
                break   # in order: a large job is not passed by the small ones behind it
            self.seats[s] += need; self.inflight[s] += 1; self.last_busy = time.time()
            self.jobs.set(j["id"], state="running", started=time.time())
            threading.Thread(target=self.run_job, args=(j, s, need), daemon=True).start()

    # chat requests (the OpenAI-compatible door)

    def acquire(self, setup):
        w = {"setup": setup, "t": time.time()}
        with self.cv:
            self.waiting.append(w); self.cv.notify_all()
            try:
                ok = self.cv.wait_for(lambda: self.admissible(setup) or self.held, REQUEST_WAIT)
                if self.held:
                    raise JobError(self.held)
                if not ok:
                    raise JobError(f"{setup} was not loaded within {REQUEST_WAIT} s")
                self.inflight[setup] += 1; self.last_busy = time.time()
            finally:
                self.waiting.remove(w)
        return self.url(setup)

    def release(self, setup):
        with self.cv:
            self.inflight[setup] -= 1; self.last_busy = time.time(); self.cv.notify_all()

    # jobs

    def submit(self, who, spec):
        kind = spec.get("kind")
        if kind not in KINDS:
            raise JobError(f"kind must be one of {', '.join(KINDS)}")
        if kind not in who.get("scopes", KINDS):
            raise PermissionError(f"this token may not start {kind} jobs")
        if len(self.jobs.find(caller=who["name"], states=("queued", "awaiting_approval"))) >= JOBS_PER_CALLER:
            raise JobError(f"{JOBS_PER_CALLER} of your jobs already wait")
        p, seats = {}, 1
        if kind == "chat":
            setup = spec.get("setup") or "main"
            if setup not in SETUPS:
                raise JobError("setup must be main, swarm or big")
            if not isinstance(spec.get("messages"), list) or not spec["messages"]:
                raise JobError("chat needs messages")
            p = {k: spec[k] for k in ("messages", "max_tokens", "thinking", "thinking_budget", "temperature") if k in spec}
            summary = f"chat on {setup}, {sum(len(str(m.get('content', ''))) for m in spec['messages'])} characters"
        elif kind == "swarm":
            setup = "swarm"
            p = {k: spec[k] for k in ("upload", "brief", "glob", "areas", "thinking", "verify") if k in spec}
            if not p.get("upload"):
                raise JobError("swarm needs an upload")
            summary = f"swarm: {(spec.get('brief') or 'bug hunt')[:120]}"
        else:
            setup = "main"
            p = {k: spec[k] for k in ("upload", "spec", "test_command", "protect", "attempts", "max_turns", "test_timeout")
                 if k in spec}
            if not (p.get("upload") and p.get("spec") and p.get("test_command")):
                raise JobError("implement needs an upload, a spec and a test_command")
            p["attempts"] = max(1, min(4, int(p.get("attempts") or 1)))
            seats = p["attempts"]
            summary = f"implement: {spec['spec'][:120]}"
        if p.get("upload"):
            self.claim_upload(who, p["upload"])
        jid = secrets.token_hex(6)
        os.makedirs(jdir(jid))
        json.dump(p, open(os.path.join(jdir(jid), "params.json"), "w"))
        held = kind in who.get("approve", [])
        row = self.jobs.add(id=jid, caller=who["name"], kind=kind, setup=setup, created=time.time(), summary=summary,
                            state="awaiting_approval" if held else "queued", seats=seats)
        if held:
            ping(f"local-compute: {who['name']} asks for a {kind} job ({summary[:200]}). Approve: lc approve {jid}  "
                 f"Refuse: lc cancel {jid}")
        with self.cv:
            self.cv.notify_all()
        return row

    def claim_upload(self, who, upload):
        meta = os.path.join(STATE, "uploads", f"{upload}.json")
        if not re.fullmatch(r"[0-9a-f]{24}", upload) or not os.path.exists(meta):
            raise JobError(f"no upload {upload}")
        if json.load(open(meta))["caller"] != who["name"]:
            raise PermissionError("that upload is not yours")

    def finish(self, jid, state, result=None, error=None):
        if result is not None:
            with open(os.path.join(jdir(jid), "result.json.new"), "w") as f:
                json.dump(result, f)
            os.replace(os.path.join(jdir(jid), "result.json.new"), os.path.join(jdir(jid), "result.json"))
        self.jobs.set(jid, state=state, finished=time.time(), error=error, progress=None)
        with self.cv:
            self.cancel.discard(jid); self.cv.notify_all()

    def run_job(self, j, setup, need):
        jid = j["id"]
        try:
            p = json.load(open(os.path.join(jdir(jid), "params.json")))
            res = getattr(self, f"run_{j['kind']}")(j, p, self.url(setup))
            if res == "waiting":
                self.jobs.set(jid, state="waiting")
                with self.cv:
                    self.cv.notify_all()
            elif jid in self.cancel:
                self.finish(jid, "cancelled", res)
            else:
                self.finish(jid, "done", res)
        except Exception as e:
            log(f"job {jid} ({j['kind']}): " + traceback.format_exc())
            self.finish(jid, "cancelled" if jid in self.cancel else "failed", error=str(e)[:500])
        finally:
            with self.cv:
                self.seats[setup] -= need; self.inflight[setup] -= 1; self.last_busy = time.time(); self.cv.notify_all()

    def progress(self, jid, text):
        self.jobs.set(jid, progress=text)

    def run_chat(self, j, p, url):
        thinking = p.get("thinking", True)
        budget = int(p.get("thinking_budget") or 8192)
        body = {"model": j["setup"], "messages": p["messages"], "temperature": p.get("temperature", 0.6), "top_p": 0.95,
                "max_tokens": int(p.get("max_tokens") or 8192) + (budget if thinking else 0),
                "chat_template_kwargs": {"enable_thinking": bool(thinking)}}
        if thinking:   # vLLM's and Strata's names for the thinking budget (the bench's lcb-quick.py)
            body["reasoning_budget_tokens" if j["setup"] == "big" else "thinking_token_budget"] = budget
        r = post(url + "/chat/completions", body)
        m = r["choices"][0]["message"]
        return {"content": m.get("content") or "", "reasoning_chars": len(m.get("reasoning_content") or m.get("reasoning") or ""),
                "finish_reason": r["choices"][0].get("finish_reason"), "usage": r.get("usage")}

    def run_swarm(self, j, p, url):
        jid, d = j["id"], jdir(j["id"])
        tgt = os.path.join(d, "target")
        unpack(p["upload"], tgt)
        globs = [g.strip() for g in str(p.get("glob") or "*.py").split(",") if g.strip()]
        files = text_files(tgt, globs)
        if not files:
            raise JobError(f"no text files match {', '.join(globs)}")
        areas, total = split_areas(files, int(p["areas"]) if p.get("areas") else None)
        json.dump(areas, open(os.path.join(d, "assign.json"), "w"))
        at = pool_fit(url, len(areas))
        kind = "lc-bughunt"
        if p.get("brief"):
            kind = f"lc-{jid}"
            scout.BRIEFS[kind] = CUSTOM.replace("{brief}", str(p["brief"])[:2000].replace("{", "{{").replace("}", "}}"))
        a = argparse.Namespace(url=url, model="swarm", target=tgt, assign=os.path.join(d, "assign.json"), agents=at,
                               out=os.path.join(d, "run.json"), cap=64000, thinking="on" if p.get("thinking") else "off",
                               max_turns=40, kind=kind, rolling=0, rules=2, events=None, label="")
        self.progress(jid, f"0/{len(areas)} areas, {at} scouts at once")
        svc = self

        class Progress:   # scout prints "[k/N]" as each scout ends
            def write(self, s):
                m = re.search(r"\[(\d+)/(\d+)\]", s)
                if m:
                    svc.progress(jid, f"{m[1]}/{m[2]} areas, {at} scouts at once")
                with open(os.path.join(d, "log.txt"), "a") as f:
                    f.write(s)

            def flush(self):
                pass

        SWARM_CANCEL.clear()
        try:
            with contextlib.redirect_stdout(Progress()):
                scout.run(a)
        finally:
            scout.BRIEFS.pop(f"lc-{jid}", None)
        run_ = json.load(open(a.out))
        s = run_["summary"]
        result = {"findings": cluster(run_["scouts"]), "reports": sum(len(sc["findings"]) for sc in run_["scouts"]),
                  "files": len(files), "lines": total, "areas": len(areas), "at_once": at, "wall_s": s["wall_s"],
                  "tokens_out": s["completion_tokens"], "ends": s["ends"], "brief": p.get("brief") or "bug hunt",
                  "verified": False}
        if jid in self.cancel:
            return result
        if p.get("verify") and result["findings"]:
            open(os.path.join(d, "result.json"), "w").write(json.dumps(result))
            vid = secrets.token_hex(6)
            os.makedirs(jdir(vid))
            json.dump({"parent": jid}, open(os.path.join(jdir(vid), "params.json"), "w"))
            self.jobs.add(id=vid, caller=j["caller"], kind="verify", setup="main", created=time.time(), state="queued",
                          summary=f"verify {len(result['findings'])} findings of {jid}", parent=jid, seats=1)
            self.progress(jid, f"swarm done; main checks {len(result['findings'])} findings (job {vid})")
            return "waiting"
        shutil.rmtree(tgt, ignore_errors=True)
        return result

    def run_verify(self, j, p, url):
        """the second tier the bench measured (scout.py verify): the 27B checks each finding against the 50 lines
        around it, thinking on; the parent swarm job ends with the verdicts"""
        parent = p["parent"]
        d = jdir(parent)
        res = json.load(open(os.path.join(d, "result.json")))
        brief = None if res["brief"] == "bug hunt" else res["brief"]
        target = scout.Target(os.path.join(d, "target"))
        done = [0]

        def stopped():   # this check, or the swarm job it checks for, was cancelled
            return j["id"] in self.cancel or self.jobs.get(parent)["state"] == "cancelled"

        def one(f):
            if stopped():
                return
            try:
                text = open(target.path(f["path"]), encoding="utf-8", errors="replace").read().splitlines()
                x, y = max(1, f["line"] - 25), min(len(text), f["line"] + 25)
                code = "\n".join(f"{'>>' if i == f['line'] else '  '}{i:5d}  {text[i - 1]}" for i in range(x, y + 1))
                q = (VERIFY_CUSTOM.format(brief=brief, path=f["path"], x=x, y=y, code=code, line=f["line"], finding=f["finding"])
                     if brief else VERIFY_BUG.format(path=f["path"], x=x, y=y, code=code, line=f["line"], finding=f["finding"]))
                ans = scout.ask(url, "main", q, True)
                head = ans.upper().lstrip("*# ")
                f["verdict"] = ("yes" if head.startswith(("BUG", "YES")) else "no" if head.startswith(("NOT", "NO"))
                                else "unclear")
                f["why"] = ans[:300]
            except Exception as e:
                f["verdict"], f["why"] = "error", str(e)[:200]
            done[0] += 1
            if done[0] % 10 == 0:
                self.progress(j["id"], f"{done[0]}/{len(res['findings'])} checked")

        with ThreadPoolExecutor(16) as pool:
            list(pool.map(one, res["findings"]))
        res["verified"] = "partial" if j["id"] in self.cancel else True
        res["kept"] = sum(f.get("verdict") == "yes" for f in res["findings"])
        res["findings"].sort(key=lambda f: (f.get("verdict") != "yes", -f["scouts"], f["path"], f["line"]))
        shutil.rmtree(os.path.join(d, "target"), ignore_errors=True)
        if self.jobs.get(parent)["state"] != "cancelled":   # a cancelled check still ends its swarm job
            self.finish(parent, "done", res)
        return {"parent": parent, "checked": len(res["findings"]), "kept": res["kept"]}

    def run_implement(self, j, p, url):
        jid, d = j["id"], jdir(j["id"])
        base = os.path.join(d, "base")
        unpack(p["upload"], base)
        n = p.get("attempts", 1)
        stop = threading.Event()
        attempts = []
        for i in range(n):
            ws = os.path.join(d, f"ws{i}")
            shutil.copytree(base, ws, symlinks=True)
            attempts.append(ws)
        self.progress(jid, f"{n} attempt(s) started")
        with ThreadPoolExecutor(n) as pool:
            futs = [pool.submit(self.attempt, jid, p, url, ws, base, stop, i) for i, ws in enumerate(attempts)]
            results = [f.result() for f in futs]
        win = next((r for r in results if r["passed"]), None)
        out = {"passed": bool(win), "attempts": [{k: r[k] for k in ("passed", "end", "turns", "tokens_out", "summary")}
                                                  for r in results]}
        if win:
            out.update(patch=win["patch"], test_output=win["test_output"], summary=win["summary"], attempt=results.index(win))
        else:   # nothing passed: every attempt's patch, for the caller to judge
            out.update(patches=[r["patch"] for r in results], test_outputs=[r["test_output"] for r in results])
        for ws in attempts + [base]:
            shutil.rmtree(ws, ignore_errors=True)
        return out

    def attempt(self, jid, p, url, ws, base, stop, i):
        target = scout.Target(ws, honest=True)   # a read stops where the tool-output cut would, and says so
        protect = p.get("protect", PROTECT)
        cmd, timeout = p["test_command"], max(10, min(1800, int(p.get("test_timeout") or 600)))
        files = [os.path.relpath(os.path.join(r, f), ws) for r, ds, fs in os.walk(ws)
                 for f in fs if not set(os.path.relpath(r, ws).split(os.sep)) & SKIP_DIRS]
        listing = "\n".join(sorted(files)[:300]) + ("\n..." if len(files) > 300 else "")
        msgs = [{"role": "system", "content": "You are a careful implementation agent working in a sandboxed copy of a "
                 "project. Make the change the spec asks for so the tests pass. Use the tools; keep replies short. "
                 f"These files are protected and cannot be changed: {', '.join(protect) or 'none'}."},
                {"role": "user", "content": f"SPEC\n{p['spec']}\n\nTest command (run it with run_tests): {cmd}\n\n"
                 f"Files:\n{listing}\n\nRead what you need, make the change, run the tests and fix until they pass, "
                 "then call finish with a one-line summary."}]
        touched, passed, test_out, summary, end = set(), False, "", "", "max_turns"
        tokens, last_prompt, nudges, refusals, warned, turns = 0, 0, 0, 0, False, 0
        for turns in range(1, int(p.get("max_turns") or 60) + 1):
            if stop.is_set() or jid in self.cancel:
                end = "stopped"; break
            body = {"model": "main", "messages": msgs, "tools": IMPL_TOOLS, "temperature": 0.6, "top_p": 0.95,
                    "max_tokens": IMPL_ANSWER + IMPL_THINK, "thinking_token_budget": IMPL_THINK,
                    "chat_template_kwargs": {"enable_thinking": True}}
            try:
                r = post(url + "/chat/completions", body)
            except urllib.error.HTTPError as e:
                end = f"http {e.code}"; break
            except Exception as e:
                end = f"error {str(e)[:80]}"; break
            u = r.get("usage") or {}
            last_prompt = u.get("prompt_tokens") or last_prompt; tokens += u.get("completion_tokens") or 0
            m = r["choices"][0]["message"]
            calls = m.get("tool_calls") or []
            msgs.append({"role": "assistant", "content": m.get("content") or "", **({"tool_calls": calls} if calls else {})})
            if not calls:
                nudges += 1
                if nudges > 3:
                    end = "no tool call"; break
                msgs.append({"role": "user", "content": "Use the tools: change the code, run_tests, and finish once they pass."})
                continue
            done = False
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                    if name in ("write_file", "edit_file"):
                        full = target.path(args["path"])
                        rel_ = os.path.relpath(full, ws)
                        if any(fnmatch.fnmatch(rel_, g) for g in protect):
                            out = f"refused: {rel_} is protected (the tests are the spec's check)"
                        elif name == "write_file":
                            os.makedirs(os.path.dirname(full), exist_ok=True)
                            open(full, "w", encoding="utf-8").write(args["content"])
                            touched.add(rel_); passed = False
                            out = f"wrote {rel_} ({args['content'].count(chr(10)) + 1} lines)"
                        else:
                            text = open(full, encoding="utf-8").read()
                            k = text.count(args["old"]) if args["old"] else 0
                            if k != 1:
                                out = f"error: old text found {k} times in {rel_}; it must appear exactly once"
                            else:
                                open(full, "w", encoding="utf-8").write(text.replace(args["old"], args["new"], 1))
                                touched.add(rel_); passed = False
                                out = f"edited {rel_}"
                    elif name == "run_tests":
                        code, test_out = sandbox(ws, cmd, timeout)
                        passed = code == 0
                        out = f"exit code {code} ({'passed' if passed else 'failed'})\n{test_out[-6000:]}"
                    elif name == "finish":
                        if passed:
                            done, summary, end = True, str(args.get("summary", ""))[:500], "finish"
                            out = "ok"
                        else:
                            refusals += 1
                            out = "The tests have not passed since your last change: run_tests, fix what fails, then finish."
                            if refusals > 2:
                                done, end = True, "finish without passing"
                    elif name in ("list_dir", "read_file", "search"):
                        out = getattr(target, name)(**{k: v for k, v in args.items() if k in ("path", "start", "lines", "pattern")})
                    else:
                        out = f"error: no tool {name}"
                except Exception as e:
                    out = f"error: {str(e)[:300]}"
                msgs.append({"role": "tool", "tool_call_id": c.get("id", name), "content": str(out)[:scout.TOOL_CHARS]})
            self.progress(jid, f"attempt {i + 1}: turn {turns}, {len(touched)} files changed, tests "
                               f"{'pass' if passed else 'not passing yet'}")
            if done:
                break
            if last_prompt + IMPL_RESERVE > IMPL_CAP:
                if warned:
                    end = "context full"; break
                warned = True
                msgs.append({"role": "user", "content": "Your context is nearly full: finish the change, run_tests, finish."})
        if touched:   # the end state, checked once more whatever the model last ran
            code, test_out = sandbox(ws, cmd, timeout)
            passed = code == 0
        if passed:
            stop.set()
        return {"passed": passed, "end": end, "turns": turns, "tokens_out": tokens, "summary": summary,
                "patch": tree_diff(base, ws, touched), "test_output": test_out[-4000:]}

    def cancel_job(self, jid):
        j = self.jobs.get(jid)
        if j["state"] in ("queued", "awaiting_approval"):
            self.finish(jid, "cancelled")
        elif j["state"] in ("running", "waiting"):
            with self.cv:
                self.cancel.add(jid)
            if j["kind"] == "swarm":
                SWARM_CANCEL.set()
            for c in self.jobs.find(states=("queued", "running")):
                if c["parent"] == jid:
                    self.cancel_job(c["id"])
            if j["state"] == "waiting":
                self.finish(jid, "cancelled")
        return self.jobs.get(jid)

    def prune(self):
        while True:
            try:
                cut = time.time() - RESULT_DAYS * 86400
                for j in self.jobs.find(states=TERMINAL, limit=100000):
                    if (j["finished"] or 0) < cut and os.path.isdir(jdir(j["id"])):
                        shutil.rmtree(jdir(j["id"]), ignore_errors=True)
                up = os.path.join(STATE, "uploads")
                for f in os.listdir(up):
                    if os.path.getmtime(os.path.join(up, f)) < time.time() - 86400:
                        os.remove(os.path.join(up, f))
            except Exception:
                log("prune: " + traceback.format_exc())
            time.sleep(3600)

    def status(self, who):
        with self.cv:
            st = {"loaded": self.loaded, "switching": self.switching, "held": self.held, "paused": self.paused,
                  "draining": self.draining, "gpu_busy_with_bench": self.gpu_busy, "inflight": dict(self.inflight),
                  "chat_waiting": len(self.waiting), "switches": self.switches[-5:]}
        q = self.jobs.find(states=("queued", "awaiting_approval", "running", "waiting"))
        st["queue"] = [{"kind": j["kind"], "setup": j["setup"], "state": j["state"], "age_s": round(time.time() - j["created"]),
                        **({"id": j["id"], "progress": j["progress"]} if j["caller"] == who["name"] or who.get("admin")
                           else {})} for j in sorted(q, key=lambda j: j["created"])]
        return st


def job_view(j, with_result=False):
    v = {k: j[k] for k in ("id", "kind", "setup", "state", "created", "started", "finished", "summary", "error",
                           "progress", "parent")}
    if with_result and j["state"] in TERMINAL:
        try:
            v["result"] = json.load(open(os.path.join(jdir(j["id"]), "result.json")))
        except (FileNotFoundError, ValueError):
            pass
    return v


SVC = None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "lcd/1"

    def log_message(self, *a):
        pass

    def who(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            return None
        hx = token_hash(h[7:].strip())
        return next((t for t in load_tokens() if secrets.compare_digest(t["sha256"], hx)), None)

    def reply(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def body(self, limit):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise JobError(f"body over {limit >> 20} MB")
        return self.rfile.read(n)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        path, _, query = self.path.partition("?")
        if path == "/health":
            return self.reply(200, {"ok": True})
        who = self.who()
        if not who:
            return self.reply(401, {"error": "a valid bearer token is needed"})
        try:
            if method == "POST" and path == "/v1/chat/completions":
                return self.chat(who)
            if method == "GET" and path == "/v1/models":
                return self.reply(200, {"object": "list", "data": [{"id": s, "object": "model", "owned_by": "local"} for s in SETUPS]})
            if method == "GET" and path == "/status":
                return self.reply(200, SVC.status(who))
            if method == "POST" and path == "/uploads":
                return self.upload(who)
            if method == "POST" and path == "/jobs":
                return self.reply(200, job_view(SVC.submit(who, json.loads(self.body(BODY_MAX) or b"{}"))))
            if method == "GET" and path == "/jobs":
                return self.reply(200, {"jobs": [job_view(j) for j in SVC.jobs.find(caller=None if who.get("admin") and "all" in query
                                                                                     else who["name"], limit=50)]})
            m = re.fullmatch(r"/jobs/([0-9a-f]{12})(/cancel|/approve)?", path)
            if m:
                j = SVC.jobs.get(m[1])
                if not j or (j["caller"] != who["name"] and not who.get("admin")):
                    return self.reply(404, {"error": "no such job"})
                if method == "GET" and not m[2]:
                    w = re.search(r"wait=(\d+)", query)
                    if w:
                        with SVC.cv:
                            SVC.cv.wait_for(lambda: SVC.jobs.get(m[1])["state"] in TERMINAL, min(int(w[1]), 600))
                    return self.reply(200, job_view(SVC.jobs.get(m[1]), True))
                if method == "POST" and m[2] == "/cancel":
                    return self.reply(200, job_view(SVC.cancel_job(m[1])))
                if method == "POST" and m[2] == "/approve":
                    if not who.get("admin"):
                        return self.reply(403, {"error": "only an admin approves"})
                    if j["state"] != "awaiting_approval":
                        return self.reply(409, {"error": f"the job is {j['state']}"})
                    SVC.jobs.set(m[1], state="queued", created=time.time())
                    with SVC.cv:
                        SVC.cv.notify_all()
                    return self.reply(200, job_view(SVC.jobs.get(m[1])))
            if method == "POST" and path.startswith("/admin/"):
                if not who.get("admin"):
                    return self.reply(403, {"error": "admin only"})
                spec = json.loads(self.body(1 << 16) or b"{}")
                with SVC.cv:
                    if path == "/admin/pause":
                        SVC.paused = True
                    elif path == "/admin/resume":
                        SVC.paused, SVC.held = False, None
                    elif path == "/admin/load":
                        if spec.get("setup") not in SETUPS:
                            return self.reply(400, {"error": "setup must be main, swarm or big"})
                        SVC.want = spec["setup"]
                    SVC.cv.notify_all()
                if path == "/admin/pause" and spec.get("stop"):
                    threading.Thread(target=self.stop_when_idle, daemon=True).start()
                return self.reply(200, SVC.status(who))
            return self.reply(404, {"error": "no such endpoint"})
        except PermissionError as e:
            return self.reply(403, {"error": str(e)})
        except (JobError, ValueError, KeyError) as e:
            return self.reply(400, {"error": str(e)})

    def stop_when_idle(self):
        with SVC.cv:
            SVC.cv.wait_for(lambda: not any(SVC.inflight.values()), 3600)
            if not SVC.paused:
                return
            SVC.loaded = None
        SVC.sh("stop", timeout=300)
        log("paused and stopped the setup")

    def upload(self, who):
        data = self.body(UPLOAD_MAX)
        uid = secrets.token_hex(12)
        dest = os.path.join(STATE, "uploads", f"{uid}.tar.gz")
        open(dest, "wb").write(data)
        try:
            with tarfile.open(dest, "r:gz") as t:
                ms = t.getmembers()
            size = sum(m.size for m in ms if m.isfile())
            if size > UNPACKED_MAX:
                raise JobError(f"unpacks to over {UNPACKED_MAX >> 20} MB")
        except (tarfile.TarError, OSError, EOFError) as e:
            os.remove(dest)
            raise JobError(f"not a .tar.gz: {e}")
        except JobError:
            os.remove(dest)
            raise
        json.dump({"caller": who["name"], "at": time.time()}, open(os.path.join(STATE, "uploads", f"{uid}.json"), "w"))
        self.reply(200, {"upload": uid, "files": sum(m.isfile() for m in ms), "bytes": size})

    def chat(self, who):
        if "chat" not in who.get("scopes", KINDS):
            return self.reply(403, {"error": "this token may not chat"})
        req = json.loads(self.body(BODY_MAX))
        setup = req.get("model") if req.get("model") in SETUPS else "main"
        t0 = time.time()
        try:
            url = SVC.acquire(setup)
        except JobError as e:
            return self.reply(503, {"error": str(e)})
        usage, code = {}, 200
        try:
            req["model"] = setup
            up = urllib.request.Request(url + "/chat/completions", data=json.dumps(req).encode(),
                                        headers={"Content-Type": "application/json"})
            try:
                r = urllib.request.urlopen(up, timeout=3600)
            except urllib.error.HTTPError as e:
                code, data = e.code, e.read()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
                return
            with r:
                if req.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", r.headers.get("Content-Type", "text/event-stream"))
                    self.send_header("Cache-Control", "no-cache"); self.end_headers()
                    while True:
                        chunk = r.read1(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk); self.wfile.flush()
                else:
                    data = r.read()
                    try:
                        usage = json.loads(data).get("usage") or {}
                    except ValueError:
                        pass
                    self.send_response(200); self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            code = 499
        finally:
            SVC.release(setup)
            with open(os.path.join(STATE, "requests.log"), "a") as f:   # no content: who, where, how much, how long
                f.write(json.dumps({"t": round(t0), "caller": who["name"], "setup": setup, "code": code,
                                    "s": round(time.time() - t0, 1), "prompt_tokens": usage.get("prompt_tokens"),
                                    "completion_tokens": usage.get("completion_tokens")}) + "\n")


def serve():
    global SVC
    SVC = Service()
    threading.Thread(target=SVC.loop, daemon=True).start()
    threading.Thread(target=SVC.prune, daemon=True).start()
    servers = [http.server.ThreadingHTTPServer(("127.0.0.1", p), Handler) for p in PORTS]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    log(f"lcd listening on 127.0.0.1:{', '.join(map(str, PORTS))}; state {STATE}")
    servers[0].serve_forever()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    t = sub.add_parser("token")
    t.add_argument("action", choices=["add", "list", "remove"])
    t.add_argument("name", nargs="?")
    t.add_argument("--admin", action="store_true")
    t.add_argument("--approve", default="", help="kinds held for an admin's approval, e.g. swarm,implement")
    t.add_argument("--scopes", default="chat,swarm,implement")
    a = ap.parse_args()
    if a.cmd == "serve":
        return serve()
    toks = load_tokens()
    if a.action == "list":
        for x in toks:
            print(f"{x['name']:12s} scopes {','.join(x['scopes'])}" + (" admin" if x.get("admin") else "")
                  + (f" approve {','.join(x['approve'])}" if x.get("approve") else ""))
        return
    if not a.name:
        sys.exit("a token name is needed")
    toks = [x for x in toks if x["name"] != a.name]
    if a.action == "add":
        raw = "lc_" + secrets.token_urlsafe(32)
        toks.append({"name": a.name, "sha256": token_hash(raw), "scopes": [s for s in a.scopes.split(",") if s],
                     "approve": [s for s in a.approve.split(",") if s], "admin": a.admin})
        print(raw)
    save_tokens(toks)


if __name__ == "__main__":
    main()
