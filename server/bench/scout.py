#!/usr/bin/env python3
"""scout: many small agents read one target at once, each its own slice, and report what they find.

A scout is a minimal agent loop, not a coding agent: read-only tools (list a folder, read lines, search), report(),
finish(), and a hard context cap the loop enforces, not the model. Tool output is cut in code; the model's thinking is
dropped from the history between turns (it is the largest thing in it and the scout never needs it again). Many
scouts run as threads of one process, so a hundred cost a few hundred MB, not a hundred coding-agent processes.
Why (2026-10-17): the lab's goal is a swarm of ~100 local scouts pointed at a target; Pi (the harness's coding agent)
is too heavy to run a hundred of on the 6-core VM, and its sessions carry far more context than a scout needs.

usage: scout.py split --target DIR --agents N --out ASSIGN.json [--glob '*.py']
       scout.py run --url http://127.0.0.1:8082/v1 --target DIR --assign ASSIGN.json --agents N --out RUN.json
                    [--cap 32000] [--thinking off|on] [--max-turns 40] [--model NAME] [--kind bughunt]
                    [--rolling N]   (keep only the last N file reads verbatim in the scout's context)
                    [--rules 2]     (the brief says to report as it goes; a read that would overfill the context is refused)
       scout.py grade --run RUN.json --manifest MANIFEST.json      (found / missed planted bugs, false reports)
       scout.py verify --run RUN.json --url URL --out RUN.verified.json [--cache FILE] [--thinking on] [--parallel 16]
                    (a stronger model checks each reported line; grade the .verified.json like a run)
       scout.py match --run RUN.json --manifest MANIFEST.json --url URL --cache FILE [--thinking on]
                    (a stronger model judges whether each report near a planted bug names it; grade --match FILE)
"""
import argparse, fnmatch, hashlib, json, os, re, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

READ_LINES = 150        # most lines one read_file returns
TOOL_CHARS = 9000       # any tool result is cut to this
SEARCH_HITS = 40
RESERVE = 3500          # tokens kept free for the answer: past cap - RESERVE the scout is told to wrap up
THINK_BUDGET = 2048     # thinking tokens per turn when thinking is on
CHARS_PER_TOKEN = 3.0   # rules-2 context estimate until a scout's own requests give its ratio (numbered code ~3.5)

# --events FILE (10-19): one JSON line per step of every scout (run, start, ask, read, search, list, report, finish, end),
# for a live view of the swarm (hive-feed on the box follows the file and ships the shape of the work to the public
# page). Off unless asked for; a scout never waits on it beyond the write.
EVENTS, EVENTS_LOCK, RUN_ID = None, threading.Lock(), None


def emit(scout_id, ev, **kw):
    if EVENTS is None:
        return
    line = json.dumps({"t": round(time.time(), 3), "run": RUN_ID, "s": scout_id, "ev": ev, **kw}, separators=(",", ":"))
    with EVENTS_LOCK:
        EVENTS.write(line + "\n"); EVENTS.flush()


def rel(target, p):
    try:
        return os.path.relpath(target.path(p), target.root)
    except ValueError:
        return "?"

BRIEFS = {
    "bughunt": (
        "You are one scout in a swarm reviewing a Python codebase for bugs. Other scouts cover the rest; cover "
        "YOUR AREA thoroughly:\n{area}\n\nRead your area with read_file (line ranges). You may read or search other "
        "files to see how something is used. For each likely bug - a wrong comparison or boundary, an inverted "
        "condition, a wrong variable, argument or index, a min/max or and/or mix-up, a wrong return value - call "
        "report(path, line, what is wrong). Report real bugs only, not style or missing features, and give the exact "
        "line. When your area is covered, call finish with a one-line summary."),
}
# Rules 2 (10-19). The A3B asks for five to ten reads in one turn and reports only after reading its whole area: at 16
# scouts on 32k all 16 went past the cap within 3-7 turns and reported nothing, and the cap, checked between turns,
# was overshot by a whole batch of reads. Under rules 2 the brief says how much context there is and to report as it
# goes, and the loop refuses a read that would overfill the context, so the cap is the same for every model.
RULES2 = (" Your context holds about {cap} tokens{roll}, so report each bug as soon as you see it, before you read on. "
          "A read that would overfill your context is refused.")

TOOLS = [
    {"type": "function", "function": {"name": "list_dir", "description": "List a folder of the target.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "read_file",
     "description": f"Read lines of a file, numbered. At most {READ_LINES} lines per call.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer"},
                                                     "lines": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "search",
     "description": f"Regex search in files under a path. Returns up to {SEARCH_HITS} matching lines (path:line: text).",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                    "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "report", "description": "Report one finding at an exact line.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "line": {"type": "integer"},
                                                     "finding": {"type": "string"}},
                    "required": ["path", "line", "finding"]}}},
    {"type": "function", "function": {"name": "finish", "description": "End: your area is covered.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]


class Target:
    """Read-only view of one folder; every path is resolved inside it."""

    def __init__(self, root, honest=False):
        self.root = os.path.realpath(root); self.honest = honest

    def path(self, p):
        full = os.path.realpath(os.path.join(self.root, (p or ".").lstrip("/")))
        if full != self.root and not full.startswith(self.root + os.sep):
            raise ValueError("outside the target")
        return full

    def list_dir(self, path="."):
        full = self.path(path)
        names = sorted(os.listdir(full))[:200]
        return "\n".join(n + ("/" if os.path.isdir(os.path.join(full, n)) else "") for n in names)

    def read_file(self, path, start=1, lines=READ_LINES):
        text = open(self.path(path), encoding="utf-8", errors="replace").read().splitlines()
        start = max(1, int(start or 1)); n = max(1, min(int(lines or READ_LINES), READ_LINES))
        end = min(len(text), start + n - 1)
        rows = [f"{i:5d}  {text[i - 1]}" for i in range(start, end + 1)]
        if self.honest:   # rules 2 (10-19): stop where the tool-output cut would, and say so. 150 lines of a file
            # with long lines pass TOOL_CHARS, and the cut hid ~4% of the target's lines behind a header claiming them
            room, used, keep = TOOL_CHARS - len(f"{path} lines {start}-{end} of {len(text)}\n"), 0, 0
            for row in rows:
                if used + len(row) + 1 > room and keep:
                    break
                used += len(row) + 1; keep += 1
            rows, end = rows[:keep], start + keep - 1
        return f"{path} lines {start}-{end} of {len(text)}\n" + "\n".join(rows)

    def search(self, pattern, path="."):
        rx = re.compile(pattern); hits = []
        base = self.path(path)
        files = [base] if os.path.isfile(base) else [os.path.join(r, f) for r, _, fs in sorted(os.walk(base)) for f in sorted(fs)]
        for f in files:
            try:
                for i, line in enumerate(open(f, encoding="utf-8", errors="replace"), 1):
                    if rx.search(line):
                        hits.append(f"{os.path.relpath(f, self.root)}:{i}: {line.strip()[:200]}")
                        if len(hits) >= SEARCH_HITS:
                            return "\n".join(hits) + "\n(more matches cut)"
            except OSError:
                continue
        return "\n".join(hits) or "no matches"


def chat(url, model, messages, max_tokens, thinking):
    body = {"model": model, "messages": messages, "tools": TOOLS, "max_tokens": max_tokens, "temperature": 0.7,
            "top_p": 0.95, "chat_template_kwargs": {"enable_thinking": thinking}}
    if thinking:
        body["thinking_token_budget"] = THINK_BUDGET
    req = urllib.request.Request(url + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)


def scout(a, target, job):
    """One scout on one assignment. Returns its findings and what it cost."""
    area = "\n".join(f"- {s}" for s in job["area"])
    msgs = [{"role": "system", "content": "You are a careful, fast code scout. Use the tools; keep replies short."},
            {"role": "user", "content": BRIEFS[a.kind].format(area=area) + (RULES2.format(
                cap=a.cap, roll=f", and only your last {a.rolling} file reads stay in it" if a.rolling else "")
                if a.rules == 2 else "")}]
    found, t0, ptok, ctok, turns, end, last_prompt, warned = [], time.monotonic(), 0, 0, 0, "max_turns", 0, False
    read, reads = [], []   # the line ranges read_file returned, and the indices of those tool messages in msgs
    sent, refused = 0, 0   # the size of the messages last sent (rules 2), and the reads refused for lack of room
    first = None           # (characters, prompt tokens) of the first request: the scale for the rules-2 estimate
    emit(job["id"], "start", area=job["area"], cap=a.cap)
    for turns in range(1, a.max_turns + 1):
        try:
            sent = size(msgs)
            emit(job["id"], "ask", turn=turns, ctx=last_prompt)
            r = chat(a.url, a.model, msgs, min(RESERVE, max(256, a.cap - last_prompt)), a.thinking == "on")
        except urllib.error.HTTPError as e:   # most often: the prompt passed the server's limit
            end = f"http {e.code}"; break
        except Exception as e:
            end = f"error {str(e)[:80]}"; break
        u = r.get("usage") or {}; last_prompt = u.get("prompt_tokens") or last_prompt
        if first is None and u.get("prompt_tokens"):
            first = (sent, last_prompt)
        ptok += u.get("prompt_tokens") or 0; ctok += u.get("completion_tokens") or 0
        m = r["choices"][0]["message"]
        calls = m.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": m.get("content") or "", **({"tool_calls": calls} if calls else {})})
        if not calls:
            end = "no tool call"; break
        done, new = False, len(msgs)   # tool messages from here on are this turn's: the model has not seen them yet
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
                if name == "report":
                    found.append({"path": args["path"].lstrip("./"), "line": int(args["line"]), "finding": args["finding"][:300]})
                    emit(job["id"], "report", turn=turns, ctx=last_prompt, **found[-1])
                    out = "recorded"
                elif name == "finish":
                    out, done = "ok", True
                    emit(job["id"], "finish", turn=turns, ctx=last_prompt)
                else:
                    out = getattr(target, name)(**{k: v for k, v in args.items() if k in ("path", "start", "lines", "pattern")})
                    m_ = re.match(r".* lines (\d+)-(\d+) of \d+", out) if name == "read_file" else None
                    if m_ and int(m_[2]) >= int(m_[1]):
                        read.append([os.path.relpath(target.path(args.get("path")), target.root), int(m_[1]), int(m_[2])])
                        reads.append(len(msgs))   # the index of the tool message appended just below
                    if name == "read_file":
                        emit(job["id"], "read", turn=turns, ctx=last_prompt, path=rel(target, args.get("path")),
                             start=int(m_[1]) if m_ else int(args.get("start") or 1), end=int(m_[2]) if m_ else 0)
                    elif name == "search":
                        emit(job["id"], "search", turn=turns, ctx=last_prompt, pattern=str(args.get("pattern", ""))[:200],
                             path=rel(target, args.get("path")) if args.get("path") else None)
                    elif name == "list_dir":
                        emit(job["id"], "list", turn=turns, ctx=last_prompt, path=rel(target, args.get("path")))
            except Exception as e:
                out = f"error: {str(e)[:200]}"
            msgs.append({"role": "tool", "tool_call_id": c.get("id", name), "content": str(out)[:TOOL_CHARS]})
        if done:
            end = "finish"; break
        if a.rolling:   # older file reads leave the context; the reports made from them are already recorded (10-18:
            # at 16 and 32 scouts the 4B's scouts filled 32k with their reads and stopped with their areas half read).
            # Only reads the model has seen go (10-19: dropping all but the last N of a turn's batch of ten reads hid
            # eight of them before the model saw them, and counted them as read)
            for i in reads[:-a.rolling]:
                if i < new and not msgs[i]["content"].startswith("[dropped"):
                    msgs[i]["content"] = f"[dropped to save context: {msgs[i]['content'].splitlines()[0]}, read earlier; your reports stand]"
        ctx, full = last_prompt, False
        if a.rules == 2:   # the next prompt: the last one, counted by the server, plus what changed since, estimated
            # at the characters per token of what this scout added since its first request (the server's counts)
            per = CHARS_PER_TOKEN
            if first and sent - first[0] > 4000 and last_prompt > first[1]:
                per = min(5.0, max(2.5, (sent - first[0]) / (last_prompt - first[1])))
            ctx = last_prompt + (size(msgs) - sent) / per
            while ctx + RESERVE > a.cap and reads and reads[-1] >= new:   # this turn's reads, newest first
                i = reads.pop(); read.pop(); refused += 1; full = not a.rolling   # without rolling no room comes back
                head = msgs[i]["content"].splitlines()[0]
                msgs[i]["content"] = (f"[refused: {head} would overfill your context ({a.cap} tokens). Report what "
                                      "you found in what you have read, then read on.]")
                ctx = last_prompt + (size(msgs) - sent) / per
        if turns % 10 == 0:   # progress now and then: the box's watchdog stops an arm whose results/ stay unchanged for
            # 90 min, and a few large scouts thinking through 100 turns can take that long to finish (10-18)
            print(f"{job['id']} turn {turns:3d} ctx {last_prompt:6d} findings {len(found):2d} {time.monotonic() - t0:7.1f}s", flush=True)
        if ctx + RESERVE > a.cap or full:   # the cap is the loop's, not the model's: one last turn to report
            if warned:
                end = "cap"; break
            warned = True
            msgs.append({"role": "user", "content": "Your context is nearly full. Report any bug you have not reported "
                                                     "yet, then call finish."})
    emit(job["id"], "end", end=end, turns=turns, findings=len(found), wall_s=round(time.monotonic() - t0, 1), ctx=last_prompt)
    return {"id": job["id"], "area": job["area"], "findings": found, "turns": turns, "end": end, "read": merged(read),
            "refused": refused,
            "last_prompt_tokens": last_prompt, "prompt_tokens": ptok, "completion_tokens": ctok,
            "wall_s": round(time.monotonic() - t0, 1)}


def size(msgs):
    """characters the model is sent for these messages (the rules-2 context estimate's unit)"""
    return sum(len(m.get("content") or "") + len(json.dumps(m["tool_calls"]) if m.get("tool_calls") else "") for m in msgs)


def merged(ranges):
    """[[path, first, last], ...] -> the same lines as sorted ranges that do not overlap"""
    out = []
    for p, x, y in sorted(ranges):
        if out and out[-1][0] == p and x <= out[-1][2] + 1:
            out[-1][2] = max(out[-1][2], y)
        else:
            out.append([p, x, y])
    return out


def split(a):
    """Cut the target's files into N slices of about equal line count (a long file is cut by line range)."""
    files = []
    for r, ds, fs in os.walk(a.target):
        ds[:] = sorted(d for d in ds if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".venv"))
        files += [os.path.relpath(os.path.join(r, f), a.target) for f in sorted(fs) if fnmatch.fnmatch(f, a.glob)]
    sizes = [(f, sum(1 for _ in open(os.path.join(a.target, f), encoding="utf-8", errors="replace"))) for f in files]
    total = sum(n for _, n in sizes); per = max(1, -(-total // a.agents))
    jobs, cur, room = [], [], per
    for f, n in sizes:
        start = 1
        while n - start + 1 > 0:
            take = min(room, n - start + 1)
            cur.append(f"{f} lines {start}-{start + take - 1}"); start += take; room -= take
            if room == 0:
                jobs.append(cur); cur, room = [], per
    if cur:
        jobs.append(cur)
    out = [{"id": f"s{i:03d}", "area": area} for i, area in enumerate(jobs)]
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"{len(out)} slices of ~{per} lines from {len(files)} files ({total} lines)")


def run(a):
    target = Target(a.target, honest=a.rules == 2)
    jobs = json.load(open(a.assign))
    lock, results, t0 = threading.Lock(), [], time.monotonic()
    global EVENTS, RUN_ID
    if a.events:
        EVENTS = open(a.events, "a"); RUN_ID = a.label or os.path.basename(a.out or "run").rsplit(".", 1)[0]
        sizes = {}   # lines per file, from the slices (split wrote "path lines a-b")
        for j in jobs:
            for s in j["area"]:
                m_ = re.match(r"(.*) lines (\d+)-(\d+)$", s)
                if m_:
                    sizes[m_[1]] = max(sizes.get(m_[1], 0), int(m_[3]))
        emit(None, "run", target=os.path.basename(target.root), root=target.root, label=a.label, agents=a.agents, cap=a.cap, thinking=a.thinking == "on",
             model=a.model, kind=a.kind, rules=a.rules, rolling=a.rolling, sizes=sizes)

    def one(job):
        res = scout(a, target, job)
        with lock:
            results.append(res)
            print(f"{res['id']} {res['end']:12s} turns {res['turns']:3d} findings {len(res['findings']):2d} "
                  f"ctx {res['last_prompt_tokens']:6d} {res['wall_s']:7.1f}s  [{len(results)}/{len(jobs)}]", flush=True)

    with ThreadPoolExecutor(a.agents) as pool:
        list(pool.map(one, jobs))
    wall = round(time.monotonic() - t0, 1)
    summary = {"agents": a.agents, "slices": len(jobs), "cap": a.cap, "thinking": a.thinking, "kind": a.kind,
               "rolling": a.rolling, "max_turns": a.max_turns, "target": a.target, "rules": a.rules,
               "refused_reads": sum(r.get("refused", 0) for r in results),
               "wall_s": wall, "completion_tokens": sum(r["completion_tokens"] for r in results),
               "prompt_tokens": sum(r["prompt_tokens"] for r in results),
               "ends": {e: sum(r["end"] == e for r in results) for e in sorted({r["end"] for r in results})}}
    json.dump({"summary": summary, "scouts": sorted(results, key=lambda r: r["id"])}, open(a.out, "w"), indent=1)
    print(json.dumps(summary))


def grade(a):
    """A planted bug is found when any scout reports the same file within 3 lines of it. Also (10-18): how many of the
    reported lines would land that near a planted bug by chance, which planted bugs any scout read at all, and how much
    of its own area each scout read: a scout that ran out of context or turns cannot be blamed for what it never saw.
    Writes RUN.grade.json beside the run."""
    run_ = json.load(open(a.run)); man = json.load(open(a.manifest)); bugs = man["bugs"]
    s = run_["summary"]; target = Target(a.target or s.get("target") or "scout/target")
    reports = [f for sc in run_["scouts"] for f in sc["findings"]]
    hit = near
    found = [b for b in bugs if any(hit(f, b) for f in reports)]
    false = [f for f in reports if not any(hit(f, b) for b in bugs)]
    judged = json.load(open(a.match)) if a.match and os.path.exists(a.match) else None
    lines = {}
    for f in reports:
        lines.setdefault((f["path"], f["line"]), f)
    chance = 0.0   # each distinct reported line moved to a random line of its file: expected reports near a planted bug
    for p, ln in lines:
        try:
            n = sum(1 for _ in open(target.path(p), encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        zone = {x for b in bugs if p.endswith(b["path"]) for x in range(b["line"] - 3, b["line"] + 4) if 1 <= x <= n}
        chance += len(zone) / max(n, 1)
    by = {}
    for b in bugs:
        k = by.setdefault(b["op"], [0, 0]); k[1] += 1; k[0] += b in found
    false_lines = {(f["path"], f["line"]) for f in false}
    print(f"{a.run}: {s['agents']} scouts, cap {s['cap']}, thinking {s['thinking']}"
          + (f", rolling {s['rolling']}" if s.get("rolling") else "") + (f", rules {s['rules']}" if s.get("rules", 1) != 1 else "")
          + f": found {len(found)}/{len(bugs)} "
          f"planted bugs; {len(false)} other reports; {len(reports)} reports total; wall {s['wall_s'] / 60:.1f} min; "
          f"{s['completion_tokens']} tokens out; ends {s['ends']}")
    print("  by kind: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(by.items())))
    print(f"  {len(lines)} distinct lines reported, {len(false_lines)} of them away from any planted bug; at random "
          f"{chance:.1f} of them would land within 3 lines of one")
    out = {"run": a.run, "summary": s, "found": [[b["path"], b["line"], b["op"]] for b in found],
           "other": [[p, ln, lines[(p, ln)]["finding"]] for p, ln in sorted(false_lines)],
           "distinct_lines": len(lines), "chance": round(chance, 2), "flags": []}
    if judged is not None:   # (10-19) a hit counts only when the judge says one of its reports names the planted change
        # (the 4B's 21 hits at 100 scouts held 3 reports of something else that sat within 3 lines of a planted bug)
        verdicts = {id(b): [judged.get(mkey(b, f)) for f in reports if hit(f, b)] for b in found}
        named = [b for b in found if any(v in ("yes", None) for v in verdicts[id(b)])]
        unjudged = sum(v is None for vs in verdicts.values() for v in vs)
        print(f"  {len(named)} of the {len(found)} found have a report that names the planted change"
              + (f" ({unjudged} hit reports not judged, counted as naming it)" if unjudged else ""))
        out.update(found_named=len(named), found_not_named=[[b["path"], b["line"], b["op"]] for b in found if b not in named],
                   hits_unjudged=unjudged)
    if run_["scouts"] and all("read" in sc for sc in run_["scouts"]):
        seen = {}
        for sc in run_["scouts"]:
            for p, x, y in sc["read"]:
                seen.setdefault(p, set()).update(range(x, y + 1))
        saw = [b for b in bugs if b["line"] in seen.get(b["path"], ())]
        cov = []
        for sc in run_["scouts"]:
            mine, size, got = {}, 0, 0
            for p, x, y in sc["read"]:
                mine.setdefault(p, set()).update(range(x, y + 1))
            for item in sc["area"]:
                m_ = re.match(r"(.+) lines (\d+)-(\d+)$", item)
                if m_:
                    span = range(int(m_[2]), int(m_[3]) + 1)
                    size += len(span); got += len(mine.get(m_[1], set()).intersection(span))
            cov.append(got / size if size else 0.0)
        cov.sort()
        read_n = sum(map(len, seen.values()))
        print(f"  read {read_n} of {man['lines']} target lines; {len(saw)} planted bugs were read by some scout, "
              f"{sum(b in found for b in saw)} of those found; each scout read a median {cov[len(cov) // 2]:.0%} of its "
              f"area (least {cov[0]:.0%})")
        out.update(read_lines=read_n, saw=len(saw), found_of_saw=sum(b in found for b in saw),
                   area_read_median=round(cov[len(cov) // 2], 3), area_read_min=round(cov[0], 3))
    # Most scouts ended at the cap: flagged whatever they read (10-19: the A3B's 16 scouts read a median 83% of their
    # areas in batches of reads, then hit the cap before reporting anything; coverage alone hid that)
    if s["ends"].get("cap", 0) > len(run_["scouts"]) / 2:
        out["flags"].append("CAP-BOUND")
    if out.get("found_named", len(found)) <= chance + 2:
        out["flags"].append("FLOOR")
    if out["flags"]:
        print("  " + " ".join(out["flags"]) + (": most scouts stopped at the context cap"
              if "CAP-BOUND" in out["flags"] else "") + ("; found no more than chance would" if "FLOOR" in out["flags"] else ""))
    json.dump(out, open(a.run[:-5] + ".grade.json", "w"), indent=1)


VERIFY = ("A scout reviewing this Python code for bugs reported the finding below. Decide whether it is a real bug: code "
          "that does the wrong thing when it runs. Style, naming, missing features, missing error handling and guesses "
          "about intent are not bugs.\n\n{path}, lines {x}-{y}, the reported line marked >>:\n\n{code}\n\n"
          "The report, at line {line}: {finding}\n\nAnswer with BUG or NOT A BUG on the first line, then one sentence why.")


def ask(url, model, prompt, thinking, max_tokens=300):
    """one question, no tools, temperature 0: the verifier's and the matcher's call"""
    body = {"model": model, "temperature": 0.0, "max_tokens": THINK_BUDGET + max_tokens if thinking else max_tokens,
            "messages": [{"role": "user", "content": prompt}], "chat_template_kwargs": {"enable_thinking": thinking}}
    if thinking:
        body["thinking_token_budget"] = THINK_BUDGET
    req = urllib.request.Request(url + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return (json.load(r)["choices"][0]["message"].get("content") or "").strip()


def near(f, b):
    """a report within 3 lines of a planted bug, in its file"""
    return f["path"].endswith(b["path"]) and abs(f["line"] - b["line"]) <= 3


def mkey(b, f):
    """the match cache's key: the planted bug, and the report by place and wording"""
    return f"{b['path']}:{b['line']}|{f['path']}:{f['line']}|{hashlib.sha1(f['finding'].encode()).hexdigest()[:12]}"


MATCH = ("A bug was planted in {path} by changing line {line} from\n    {before}\nto\n    {after}\n\nA code reviewer "
         "reported, at line {rline}: {finding}\n\nDoes the report identify this planted change: the same defect, even if "
         "worded differently or a line or two off? A report of a different problem nearby does not count. Answer YES "
         "or NO on the first line, then one sentence why.")


def match(a):
    """(10-19) Whether each report within 3 lines of a planted bug names that bug or something else near it. The
    judge sees the planted change (the manifest's before and after) and the report; verdicts are cached by bug, report
    line and wording in --cache, which grade --match reads."""
    run_ = json.load(open(a.run)); bugs = json.load(open(a.manifest))["bugs"]
    cache = json.load(open(a.cache)) if os.path.exists(a.cache) else {}
    todo = {}
    for sc in run_["scouts"]:
        for f in sc["findings"]:
            for b in bugs:
                if near(f, b) and mkey(b, f) not in cache:
                    todo.setdefault(mkey(b, f), (b, f))
    lock, t0 = threading.Lock(), time.monotonic()

    def one(item):
        k, (b, f) = item
        try:
            ans = ask(a.url, a.model, MATCH.format(path=b["path"], line=b["line"], before=b["before"].strip(),
                                                    after=b["after"].strip(), rline=f["line"], finding=f["finding"]),
                      a.thinking == "on")
        except Exception as e:
            print(f"  match error {k}: {str(e)[:120]}", flush=True); return
        head = ans.upper().lstrip("*# ")
        with lock:
            cache[k] = "yes" if head.startswith("YES") else "no" if head.startswith("NO") else "unclear"

    with ThreadPoolExecutor(a.parallel) as pool:
        list(pool.map(one, todo.items()))
    json.dump(cache, open(a.cache + ".new", "w"), indent=0); os.replace(a.cache + ".new", a.cache)
    keys = {mkey(b, f) for sc in run_["scouts"] for f in sc["findings"] for b in bugs if near(f, b)}
    tally = {}
    for k in keys:
        tally[cache.get(k, "error")] = tally.get(cache.get(k, "error"), 0) + 1
    print(f"{a.run}: {len(keys)} reports near a planted bug, {len(todo)} judged now ({time.monotonic() - t0:.0f} s): {tally}")


def verify(a):
    """The swarm's second tier (10-18): a stronger model checks each distinct reported line against the 50 lines around
    it, and only the reports it calls bugs are kept (RUN.verified.json, graded like a run). Verdicts are cached by
    path:line in --cache, so the many runs that report the same lines are checked once, with the first report's wording."""
    run_ = json.load(open(a.run)); s = run_["summary"]; target = Target(a.target or s.get("target") or "scout/target")
    cache = json.load(open(a.cache)) if a.cache and os.path.exists(a.cache) else {}
    todo = {}
    for sc in run_["scouts"]:
        for f in sc["findings"]:
            if f"{f['path']}:{f['line']}" not in cache:
                todo.setdefault(f"{f['path']}:{f['line']}", f)
    lock, done, t0 = threading.Lock(), [0], time.monotonic()

    def save():
        if a.cache:
            json.dump(cache, open(a.cache + ".new", "w"), indent=0); os.replace(a.cache + ".new", a.cache)

    def one(item):
        k, f = item
        try:
            text = open(target.path(f["path"]), encoding="utf-8", errors="replace").read().splitlines()
        except (OSError, ValueError):
            text = []
        x, y = max(1, f["line"] - 25), min(len(text), f["line"] + 25)
        if x > y:
            v = {"verdict": "not a bug", "why": "no such file or line"}
        else:
            code = "\n".join(f"{'>>' if i == f['line'] else '  '}{i:5d}  {text[i - 1]}" for i in range(x, y + 1))
            try:
                ans = ask(a.url, a.model, VERIFY.format(path=f["path"], x=x, y=y, code=code, line=f["line"],
                                                        finding=f["finding"]), a.thinking == "on")
                head = ans.upper().lstrip("*# ")
                v = {"verdict": "not a bug" if head.startswith("NOT") else "bug" if head.startswith("BUG") else "unclear",
                     "why": ans[:300]}
            except Exception as e:
                v = {"verdict": "error", "why": str(e)[:200]}
        with lock:
            if v["verdict"] != "error":
                cache[k] = v
            done[0] += 1
            if done[0] % 25 == 0:
                print(f"  verified {done[0]}/{len(todo)} {time.monotonic() - t0:7.1f}s", flush=True); save()

    with ThreadPoolExecutor(a.parallel) as pool:
        list(pool.map(one, todo.items()))
    save()
    keep = lambda f: cache.get(f"{f['path']}:{f['line']}", {}).get("verdict") == "bug"
    out = {"summary": {**s, "verified_by": a.label, "verified_thinking": a.thinking},
           "scouts": [{**sc, "findings": [f for f in sc["findings"] if keep(f)]} for sc in run_["scouts"]]}
    json.dump(out, open(a.out, "w"), indent=1)
    keys = {f"{f['path']}:{f['line']}" for sc in run_["scouts"] for f in sc["findings"]}
    tally = {}
    for k in keys:
        tally[cache.get(k, {}).get("verdict", "error")] = tally.get(cache.get(k, {}).get("verdict", "error"), 0) + 1
    print(f"{a.run}: {len(keys)} distinct reported lines, {len(todo)} checked now ({time.monotonic() - t0:.0f} s): {tally}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["split", "run", "grade", "verify", "match"])
    ap.add_argument("--target"); ap.add_argument("--agents", type=int, default=16); ap.add_argument("--out")
    ap.add_argument("--glob", default="*.py"); ap.add_argument("--assign"); ap.add_argument("--url")
    ap.add_argument("--model", default="bench"); ap.add_argument("--cap", type=int, default=32000)
    ap.add_argument("--thinking", choices=["off", "on"], default="off"); ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--kind", default="bughunt", choices=sorted(BRIEFS)); ap.add_argument("--run"); ap.add_argument("--manifest")
    ap.add_argument("--rolling", type=int, default=0); ap.add_argument("--cache"); ap.add_argument("--label", default="")
    ap.add_argument("--parallel", type=int, default=16); ap.add_argument("--rules", type=int, default=1, choices=[1, 2])
    ap.add_argument("--match")
    ap.add_argument("--events", help="run: append one JSON line per step of every scout here (a live view reads it)")
    a = ap.parse_args()
    {"split": split, "run": run, "grade": grade, "verify": verify, "match": match}[a.cmd](a)


if __name__ == "__main__":
    main()
