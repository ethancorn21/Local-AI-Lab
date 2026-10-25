"""view: what the local-compute service shows the world, for the public swarm page (~/code/projects/swarm-view).

Why (2026-10-25, the owner): a read-only page on his site where anyone can watch the local agents work, and that a
monitor at home can show so he sees the lab is busy. lcd tells this module what every agent does (each scout of a
swarm job, each attempt of an implement job, each check of the 27B, each chat) and the view keeps the lab as the page
draws it, plus a ring of recent events, shaped first by each job's policy:

  hidden  nothing of the job enters the view: no agents, no files (the queue shows a private job, its kind withheld)
  shape   the default. The shape of the work: files become f001.py in d01/d02 (numbered per job, extension kept), no
          text of any kind (no lines read, findings, diffs, test output, prompts, answers or search patterns); states,
          counts, line ranges, verdicts, pass or fail and tool names stay
  public  relative paths and the text: the first 40 lines of each read, findings, diffs, the end of each test run, a
          check's reason, a chat job's answer. For code that is public anyway: the caller asks with "public": true,
          and only a token marked public may (lcd.py token set <name> --public)

Every string that enters goes through scrub() (absolute paths, addresses, host names, e-mails, key-shaped strings);
the feeder (swarm-view feed/lc-feed.py) scrubs again before anything leaves the box, and the page sets all of it as
text, never HTML. Door chats (the OpenAI door) are never public: they show as agents thinking and the names of the
tools they call. The page reads the view through the feeder: GET /view on lcd, for a token with the view scope.

A session is the page's "run": it starts when work starts after a quiet spell and ends GAP seconds after the last
agent stops; the page then plays the last public session again. Ended jobs stay on the page for LINGER seconds.
"""
import collections, os, re, threading, time

SCRUB = [
    (re.compile(r"(?<![\w/])/(?:home|opt|Users|root|var|etc|tmp|mnt|srv|media|usr/local|private)/[^\s\"'`,;)\]]+"), "<path>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<addr>"),
    (re.compile(r"\b(?:[0-9a-f]{0,4}:){3,7}[0-9a-f]{1,4}\b", re.I), "<addr>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<mail>"),
    (re.compile(r"\b[\w-]+\.(?:ts\.net|local|lan|internal|home)\b", re.I), "<host>"),
    (re.compile(r"\b(?:sk|ghp|gho|ghu|ghs|xox[abp]|AKIA|AIza|hf|lc)[_-]?[A-Za-z0-9_-]{12,}"), "<key>"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "<hex>"),
    (re.compile(r"\b[A-Za-z0-9+=_-]{48,}\b"), "<token>"),   # no slash: a long relative path is not a token (10-25)
]
WINDOW, LINE_CHARS, TEXT_CHARS, NOTE_CHARS, DIFF_LINES = 40, 200, 4000, 600, 30
LINGER = int(os.environ.get("LC_VIEW_LINGER", "120"))   # s an ended job (its agents, its files) stays on the page
GAP = int(os.environ.get("LC_VIEW_GAP", "90"))          # s without a working agent before the session ends
RING = 3000            # recent events kept for the feeder
MAX_FILES = 1500       # a job's territory; files past this are not drawn
MAX_AGENTS = 800       # agents on the page; the oldest ended go first
DOOR = "door"          # the pseudo-job of the door's chats
END = {"finish": "done", "no tool call": "done", "stopped": "done", "cap": "cap", "context full": "cap",
       "max_turns": "max", "pass": "pass", "fail": "fail"}
TOOL = re.compile(r"[^\w.-]")


def scrub(s, n):
    s = str(s)
    for rx, rep in SCRUB:
        s = rx.sub(rep, s)
    s = "".join(c if c.isprintable() or c in "\n\t" else " " for c in s)
    return s[:n]


def merged(ranges):
    out = []
    for x, y in sorted(ranges):
        if out and x <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], y)
        else:
            out.append([x, y])
    return out


def profile(lines, unit=10):
    """one digit per line, its length in units (at most 9): the page draws each file as a minimap of it"""
    return "".join(str(min(9, round(len(l) / unit))) for l in lines)[:4000]


def norm(p):
    p = str(p or "").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return os.path.normpath(p.lstrip("/")) if p else "."


class View:
    def __init__(self):
        self.lock = threading.RLock()
        self.seq = 0
        self.ring = collections.deque(maxlen=RING)   # (seq, event)
        self.session = None         # {"id", "t0", "done"}
        self.jobs = {}              # lcd's job id -> the job as shown ({"hidden": True} or {"alias": parent id})
        self.agents = {}            # page id ("s001") -> agent
        self.keys = {}              # (job id, lcd's name for the agent) -> page id
        self.files = {}             # page key ("swarm 3/d01/f002.py") -> file
        self.profiles, self.pv = {}, 0
        self.last_work = 0.0
        self.n_agent = self.n_job = 0

    # ---- jobs ----
    def job_start(self, jid, kind, setup, policy, label=None, task=None, caller=None, parent=None):
        """a job starts; parent: a verify job's swarm, whose page entry it joins (its checks land on the swarm's files)"""
        with self.lock:
            if policy == "hidden":
                self.jobs[jid] = {"hidden": True}
                return
            if parent and parent in self.jobs:
                self.jobs[jid] = {"alias": parent}
                pj = self.real(parent)
                if pj:
                    pj["state"] = "checking"
                return
            self.touch()
            self.n_job += 1
            shown = scrub(label or "public code", 60) if policy == "public" else None
            if policy == "public":   # the repository's name (a path prefix on the page: no slash); the folder in label
                tag = shown.split("/")[0].strip() or "public code"
                taken = {j.get("tag") for j in self.jobs.values()}
                tag = next(t for t in [tag] + [f"{tag} {i}" for i in range(2, 99)] if t not in taken)
            else:
                tag = f"{kind} {self.n_job}"
            self.jobs[jid] = {"id": jid, "tag": tag, "label": shown, "kind": kind, "setup": setup, "policy": policy, "caller": caller,
                              "task": scrub(task, 240) if (task and policy == "public") else None, "started": time.time(),
                              "state": "running", "ended": None, "root": None, "names": {}, "dirs": {}, "agents": 0}

    def real(self, jid):
        j = self.jobs.get(jid)
        while j and "alias" in j:
            j = self.jobs.get(j["alias"])
        return j if j and "tag" in j else None

    def tag(self, jid):
        with self.lock:
            j = self.real(jid)
            return j["tag"] if j else None

    def job_files(self, jid, sizes, root=None):
        """the job's territory: {relative path: lines}; root: where public text is read from"""
        with self.lock:
            j = self.real(jid)
            if not j:
                return
            j["root"] = os.path.realpath(root) if root else None
            for p, n in sorted(sizes.items(), key=lambda x: -x[1])[:MAX_FILES]:
                k = self.path(j, p)
                self.files.setdefault(k, {"lines": int(n), "read": 0, "found": 0, "r": [], "job": j["tag"]})
                lines = self.lines(j, p) if j["policy"] == "public" else None
                if lines is not None:
                    self.profiles[k] = profile(lines)
            self.pv += 1

    def job_wait(self, jid, state):
        with self.lock:
            j = self.real(jid)
            if j:
                j["state"] = state

    def job_end(self, jid, state):
        with self.lock:
            j = self.jobs.get(jid)
            if not j or "alias" in j:   # a verify job: its swarm ends with it (lcd ends both)
                return
            if j.get("hidden"):
                self.jobs.pop(jid, None)
                return
            j["state"], j["ended"] = state, time.time()
            for sid, a in self.agents.items():
                if a["jid"] == jid and a["end"] is None:
                    a["st"] = a["end"] = "done" if state == "done" else "stop"
            self.touch()

    # ---- the policy, applied ----
    def path(self, j, p):
        p = norm(p)
        if j["policy"] == "public":
            return f"{j['tag']}/{scrub(p, 160)}"
        if p not in j["names"]:   # numbered files in numbered folders, extension kept, in order of first sight
            parts = p.split("/")
            ds = []
            for i in range(len(parts) - 1):
                d = "/".join(parts[:i + 1])
                j["dirs"].setdefault(d, f"d{len(j['dirs']) + 1:02d}")
                ds.append(j["dirs"][d])
            ext = os.path.splitext(parts[-1])[1][:8]
            j["names"][p] = "/".join(ds + [f"f{len(j['names']) + 1:03d}{ext}"])
        return f"{j['tag']}/{j['names'][p]}"

    def lines(self, j, p, root=None):
        """a file's lines from the job's tree: public jobs only; None when it cannot or must not be read"""
        root = os.path.realpath(root) if root else j.get("root")
        if j["policy"] != "public" or not root:
            return None
        full = os.path.realpath(os.path.join(root, norm(p)))
        if not full.startswith(root + os.sep):
            return None
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                return f.read(4 << 20).split("\n")
        except OSError:
            return None

    def text(self, j, s, n):
        return scrub(s, n) if j["policy"] == "public" and s else None

    # ---- agents and their events ----
    def touch(self):
        now = time.time()
        if self.session is None or self.session["done"]:   # a new session: nothing of the last one stays
            self.session = {"id": f"lab-{int(now)}", "t0": now, "done": False}
            self.drop({jid for jid, j in self.jobs.items() if j.get("ended") or jid == DOOR})
        self.last_work = now

    def agent(self, j, jid, key, role):
        k = (jid, key)
        sid = self.keys.get(k)
        if sid is None or sid not in self.agents:
            self.n_agent += 1
            sid = f"s{self.n_agent:03d}"
            self.keys[k] = sid
            j["agents"] += 1
            self.agents[sid] = {"st": "wait", "file": None, "ln": None, "turn": 0, "found": 0, "end": None,
                                "t": time.time(), "job": j["tag"], "role": role, "jid": j["id"]}
        return sid

    def expect(self, jid, keys, role):
        """agents that will start (a swarm's areas past the scouts at once): on the page from the start, waiting"""
        with self.lock:
            j = self.real(jid)
            if j:
                for key in keys:
                    self.agent(j, j["id"], key, role)

    def ev(self, jid, key, ev, role="scout", root=None, **kw):
        with self.lock:
            if not self.real(jid):
                return None
            self.touch()   # (a new session drops what ended in the last one, the door's pseudo-job with it)
            j = self.real(jid)
            if not j:
                return None
            sid = self.agent(j, j["id"], key, role)
            a = self.agents[sid]
            a["t"] = t = time.time()
            if a["end"] is not None and ev in ("start", "ask"):   # a door conversation's next request
                a["end"] = None
            out = {"t": round(t, 2), "s": sid, "ev": ev, "job": j["tag"]}
            if ev == "start":
                a["st"] = "think"
            elif ev == "ask":
                a["st"] = "think"; a["turn"] = int(kw.get("turn") or a["turn"] + 1)
                out["turn"] = a["turn"]
            elif ev == "read":
                f = self.path(j, kw.get("path")); x, y = int(kw.get("start") or 1), int(kw.get("end") or 0)
                a["st"], a["file"], a["ln"] = "read", f, ([x, y] if y >= x else None)
                if y >= x:
                    fi = self.files.setdefault(f, {"lines": self.size(j, kw.get("path"), root) or y, "read": 0, "found": 0,
                                                   "r": [], "job": j["tag"]})
                    fi["r"] = merged(fi["r"] + [[x, y]])[:60]
                    fi["read"] = sum(b - c + 1 for c, b in fi["r"]); fi["lines"] = max(fi["lines"], y)
                out.update(path=f, ln=a["ln"])
                ls = self.lines(j, kw.get("path"), root) if y >= x else None
                if ls is not None:
                    out["text"] = "\n".join(scrub(l, LINE_CHARS) for l in ls[x - 1:min(y, x + WINDOW - 1)])[:TEXT_CHARS]
            elif ev == "search":
                a["st"] = "search"
                f = self.path(j, kw["path"]) if kw.get("path") else None
                if f:
                    a["file"] = f
                out.update(path=f, text=self.text(j, kw.get("pattern"), 80))
            elif ev == "list":
                a["st"] = "list"; out["path"] = self.path(j, kw.get("path") or ".")
            elif ev == "report":
                f = self.path(j, kw.get("path")); a["st"], a["file"] = "report", f; a["found"] += 1
                self.files.setdefault(f, {"lines": 0, "read": 0, "found": 0, "r": [], "job": j["tag"]})["found"] += 1
                out.update(path=f, line=int(kw.get("line") or 0), text=self.text(j, kw.get("finding"), NOTE_CHARS))
            elif ev == "edit":   # write_file or edit_file of the implementation agent
                f = self.path(j, kw.get("path")); a["st"], a["file"] = "edit", f; a["found"] += 1
                old = str(kw.get("old") or "").split("\n") if kw.get("old") is not None else []
                new = str(kw.get("new") or "").split("\n")
                out.update(path=f, plus=len(new), minus=len(old))
                if j["policy"] == "public":
                    diff = [f"- {l}" for l in old] + [f"+ {l}" for l in new]
                    out["text"] = "\n".join(scrub(l, LINE_CHARS) for l in diff[:DIFF_LINES])[:TEXT_CHARS]
                self.files.setdefault(f, {"lines": max(1, len(new)), "read": 0, "found": 0, "r": [], "job": j["tag"]})
            elif ev == "test":
                ok = bool(kw.get("passed"))
                a["st"] = "pass" if ok else "fail"
                out.update(ok=ok, code=kw.get("code"))
                if j["policy"] == "public" and kw.get("output"):
                    out["text"] = "\n".join(scrub(l, LINE_CHARS) for l in str(kw["output"]).rstrip().split("\n")[-20:])
            elif ev == "check":
                f = self.path(j, kw.get("path")); a["st"], a["file"] = "check", f
                v = kw.get("verdict") if kw.get("verdict") in ("yes", "no", "unclear", "error") else "unclear"
                a["found"] += v == "yes"
                out.update(path=f, line=int(kw.get("line") or 0), verdict=v, text=self.text(j, kw.get("why"), NOTE_CHARS))
            elif ev == "answer":
                a["st"] = "answer"
                out.update(tokens=int(kw.get("tokens") or 0), text=self.text(j, kw.get("text"), NOTE_CHARS))
            elif ev == "call":
                names = [TOOL.sub("", str(n))[:32] for n in (kw.get("tools") or [])][:8]
                a["st"] = "call"; out["tools"] = [n for n in names if n]
            elif ev == "finish":
                a["st"] = "done"
            elif ev == "end":
                e = str(kw.get("end") or "")
                a["st"] = a["end"] = END.get(e, "err" if e.startswith(("http", "error")) else "done")
                a["turn"] = int(kw.get("turns") or a["turn"])
                out.update(end=a["end"], turns=a["turn"], found=a["found"])
            else:
                return None
            out = {k: v for k, v in out.items() if v is not None}   # a shape job's events carry no text key at all
            self.seq += 1
            self.ring.append((self.seq, out))
            return out

    def size(self, j, p, root):
        ls = self.lines(j, p, root)
        if ls is not None:
            return len(ls)
        root = os.path.realpath(root) if root else j.get("root")
        if not root:
            return 0
        full = os.path.realpath(os.path.join(root, norm(p)))
        try:   # shape: the length only, never the text
            return sum(1 for _ in open(full, "rb")) if full.startswith(root + os.sep) else 0
        except OSError:
            return 0

    # ---- the door's chats ----
    def door_start(self, caller, policy, messages):
        """a chat through the OpenAI door: one agent per conversation (its first two messages name it); -> a key"""
        if policy == "hidden":
            return None
        with self.lock:
            self.touch()
            if DOOR not in self.jobs:
                self.jobs[DOOR] = {"id": DOOR, "tag": DOOR, "kind": "chat", "setup": None, "policy": "shape",
                                   "caller": None, "task": None, "started": time.time(), "state": "running", "ended": None,
                                   "root": None, "names": {}, "dirs": {}, "agents": 0}
            head = repr([(m.get("role"), str(m.get("content"))[:2000]) for m in (messages or [])[:2] if isinstance(m, dict)])
            key = f"{caller}:{hash(head) & 0xffffffff:08x}"
            self.ev(DOOR, key, "start", role="chat")
            return key

    def door_end(self, key, ok, usage, tools):
        if key is None:
            return
        with self.lock:
            if tools:
                self.ev(DOOR, key, "call", role="chat", tools=tools)
            self.ev(DOOR, key, "answer", role="chat", tokens=(usage or {}).get("completion_tokens") or 0)
            self.ev(DOOR, key, "end", role="chat", end="finish" if ok else "error")

    # ---- what the feeder reads ----
    def prune(self, now):
        gone = {jid for jid, j in self.jobs.items() if j.get("ended") and now - j["ended"] > LINGER}
        door = self.jobs.get(DOOR)
        if door and not door.get("ended"):
            stale = [sid for sid, a in self.agents.items() if a["jid"] == DOOR and a["end"] and now - a["t"] > LINGER]
            for sid in stale:
                del self.agents[sid]
            if not any(a["jid"] == DOOR for a in self.agents.values()):
                gone.add(DOOR)
        self.drop(gone)
        if len(self.agents) > MAX_AGENTS:
            ended = sorted((a["t"], s) for s, a in self.agents.items() if a["end"])
            for _, s in ended[:len(self.agents) - MAX_AGENTS]:
                del self.agents[s]

    def drop(self, gone):
        """jobs off the page, with their agents and files"""
        if not gone:
            return
        tags = {self.jobs[j]["tag"] for j in gone if "tag" in self.jobs.get(j, {})}
        self.agents = {s: a for s, a in self.agents.items() if a["jid"] not in gone}
        self.files = {k: f for k, f in self.files.items() if f["job"] not in tags}
        self.profiles = {k: v for k, v in self.profiles.items() if k in self.files}
        for j in gone:
            self.jobs.pop(j, None)
        self.jobs = {k: v for k, v in self.jobs.items() if v.get("alias") not in gone}
        self.keys = {k: s for k, s in self.keys.items() if s in self.agents}
        self.pv += 1

    def snapshot(self, lab):
        now = time.time()
        with self.lock:
            self.prune(now)
            working = (any(a["end"] is None and a["st"] != "wait" for a in self.agents.values())
                       or any("tag" in j and not j["ended"] and j["tag"] != DOOR for j in self.jobs.values())
                       or lab.get("state") == "switching")   # a switch for queued work is part of the work
            if working:
                self.last_work = now
            elif self.session and not self.session["done"] and now - self.last_work > GAP:
                self.session["done"] = True
            shown = [j for j in self.jobs.values() if "tag" in j]
            agents = {s: {k: v for k, v in a.items() if k != "jid"} for s, a in self.agents.items()}
            live = [a for a in self.agents.values() if a["end"] is None]
            files = list(self.files.values())
            public = any(j["policy"] == "public" for j in shown)
            run = None if not self.session else {
                "id": self.session["id"], "label": "the home lab", "policy": "public" if public else "shape", "kind": "code",
                "task": "", "model": lab.get("setup") or "", "agents": len(agents), "cap": 0, "started": self.session["t0"],
                "files": len(files), "lines": sum(f["lines"] for f in files), "done": self.session["done"]}
            totals = {"active": sum(a["st"] != "wait" for a in live), "waiting": sum(a["st"] == "wait" for a in live),
                      "done": len(agents) - len(live), "findings": sum(a["found"] for a in self.agents.values()),
                      "read_lines": sum(f["read"] for f in files), "pages": 0,
                      "turns": sum(a["turn"] for a in self.agents.values())}
            jobs = {j["tag"]: {"kind": j["kind"], "setup": j["setup"], "policy": j["policy"], "caller": j["caller"], "label": j.get("label"),
                               "task": j["task"], "state": j["state"], "started": round(j["started"]),
                               "ended": round(j["ended"]) if j["ended"] else None, "agents": j["agents"]} for j in shown}
            return {"run": run, "scouts": agents, "files": {k: {x: v for x, v in f.items()} for k, f in self.files.items()},
                    "totals": totals, "lab": {**lab, "jobs": jobs}}

    def frame(self, since, pv, lab):
        """what GET /view returns: the snapshot, the events after since (all of them when lcd restarted since the
        feeder's last ask), the profiles when they changed since pv"""
        with self.lock:
            snap = self.snapshot(lab)
            reset = since > self.seq
            evs = [e for s, e in self.ring if reset or s > since][-500:]
            out = {"seq": self.seq, "snap": snap, "events": evs, "pv": self.pv, "reset": reset}
            if pv != self.pv:
                out["profiles"] = dict(self.profiles)
            return out
