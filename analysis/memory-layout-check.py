#!/usr/bin/env python3
"""memory-layout-check.py <checkout>... : is the PITFALLS.md memory layout working in these checkouts?

Files: PITFALLS.md present and in sync (pitfalls-sync on a copy changes nothing), its entries, Unsorted, entries
lacking keys, duplicate entry headings (union-merge damage), the fixed sections; no PITFALL left in DECISIONS.md;
AGENTS.md is the installed template; the union merge rule for PITFALLS.md.
Logs, for the sessions since the cut-over (the ledger records PITFALLS.md's size): sync lines and warnings in the
loop log; each session's PROGRESS.md carried its own task's journal; what each agent read: the contents block, and
whole reads that the rules replace with searches (DECISIONS.md, PITFALLS.md, PLAN.md outside 000/999); new
PITFALLS.md entries with their Where:/Symptom: keys; PITFALLs still written to DECISIONS.md.
Prints PASS/WARN/FAIL lines; exit 1 if any FAIL.
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile

HOME = os.path.expanduser("~")
SYNC = os.environ.get("PITFALLS_SYNC", os.path.join(HOME, "bin/pitfalls-sync"))
TEMPLATE = os.environ.get("AGENTS_TEMPLATE", os.path.join(HOME, ".agent-kit/template/AGENTS.md"))
FIXED = ["Agent tools and harness", "This machine and its environment", "Libraries and frameworks", "This codebase"]
fails = []


def say(level, msg):
    print(f"{level:4} {msg}")
    if level == "FAIL":
        fails.append(msg)


def git(proj, *args):
    return subprocess.run(["git", "-C", proj, *args], capture_output=True, text=True).stdout


def files(proj):
    p = os.path.join(proj, "PITFALLS.md")
    if not os.path.exists(p):
        return say("FAIL", "no PITFALLS.md")
    t = open(p).read()
    heads = re.findall(r"(?m)^#### (.*)$", t)
    toc = t[t.find("<!-- contents:start"): t.find("<!-- contents:end")]
    unsorted = re.search(r"^- Unsorted \((\d+)\)", toc, re.M)
    lacking = re.search(r"^(\d+) entr", toc, re.M)
    say("PASS", f"PITFALLS.md: {len(heads)} entries, {len(t) // 1024} KB; Unsorted {unsorted.group(1) if unsorted else 0}, "
                f"lacking Where:/Symptom: {lacking.group(1) if lacking else 0}")
    dups = sorted({h for h in heads if heads.count(h) > 1})
    say("FAIL" if dups else "PASS", f"duplicate entry headings: {len(dups)}" + (f" ({dups[0][:70]} ...)" if dups else ""))
    missing = [s for s in FIXED if f"\n## {s}\n" not in t]
    say("FAIL" if missing else "PASS", f"fixed sections present" + (f": missing {missing}" if missing else ""))
    d = tempfile.mkdtemp()
    for f in ("PITFALLS.md", "DECISIONS.md"):
        if os.path.exists(os.path.join(proj, f)):
            shutil.copy(os.path.join(proj, f), d)
    rc = subprocess.run(["python3", SYNC, d], capture_output=True).returncode
    shutil.rmtree(d)
    say({0: "PASS", 3: "WARN"}.get(rc, "FAIL"), f"pitfalls-sync on a copy: rc {rc}" +
        {0: " (in sync)", 3: " (out of sync: fine mid-session, not between sessions)"}.get(rc, " (error)"))
    dec = open(os.path.join(proj, "DECISIONS.md")).read()
    left = re.findall(r"(?m)^## .*\b(?:PITFALL|LESSON)\b.*$", dec)
    say("WARN" if left else "PASS", f"DECISIONS.md: {len(dec) // 1024} KB, PITFALL headings left: {len(left)}")
    same = os.path.exists(TEMPLATE) and open(os.path.join(proj, "AGENTS.md")).read() == open(TEMPLATE).read()
    say("PASS" if same else "WARN", "AGENTS.md is the installed template" if same else "AGENTS.md differs from the installed template")
    ga = os.path.join(proj, ".gitattributes")
    if os.path.exists(ga) and "DECISIONS.md merge=union" in open(ga).read():   # a team checkout
        say("PASS" if "PITFALLS.md merge=union" in open(ga).read() else "FAIL", "union merge rule for PITFALLS.md")


MEMREAD = re.compile(r"(AGENTS|PROGRESS|PLAN|DECISIONS|CODEMAP|PITFALLS)\.md|tasks/|git log")


def reads(path, task):
    """-> (contents block read, [whole reads at spawn: rule breaks], searches, [whole reads later, e.g. before an
    edit]) for one session log. Spawn ends at the first tool call that is not about a memory file."""
    contents, breaks, searches, later, spawn = False, [], 0, [], True
    planner = bool(re.match(r"tasks/(000|999)-", task or ""))
    plan_whole = False
    for line in open(path, errors="replace"):
        if '"tool_execution_start"' not in line:
            continue
        e = json.loads(line)
        a, name = e.get("args") or {}, e.get("toolName")
        cmd, p = str(a.get("command", "")), str(a.get("path", ""))
        if spawn and not MEMREAD.search(json.dumps(a)):
            spawn = False
        if "contents:end" in cmd or (name == "read" and p.endswith("PITFALLS.md") and a.get("limit") and int(a["limit"]) <= 80):
            contents = True
            continue
        for f in ("DECISIONS.md", "PITFALLS.md", "PLAN.md"):
            whole = (name == "read" and p.endswith(f) and not a.get("limit")) or \
                    (name == "bash" and re.search(rf"\bcat\s+(?!>)[^|;&>]*{re.escape(f)}(\s|$)", cmd) and "|" not in cmd)
            if (name == "bash" and f in cmd and re.search(r"\b(grep|rg|awk)\b", cmd)) or (name == "read" and p.endswith(f) and a.get("limit")):
                searches += 1
            if whole:
                if f == "PLAN.md" and planner:
                    plan_whole = True
                elif spawn:
                    breaks.append(f"whole read of {f}")
                else:
                    later.append(f)
    if planner and not plan_whole:
        breaks.append("planning/goal task did not read PLAN.md whole")
    return contents, sorted(set(breaks)), searches, later


def logs(proj):
    led = [json.loads(l) for l in open(os.path.join(proj, ".agent/iterations.jsonl"))]
    after = [r for r in led if "PITFALLS.md" in (r.get("memory_bytes") or {})]
    if not after:
        return say("WARN", "no session since the cut-over yet")
    first = after[0]
    say("PASS", f"{len(after)} sessions since the cut-over (iteration {first['iter']}, {first['start']}), "
                f"harness {sorted({r.get('harness_id') for r in after})}")
    log = open(os.path.join(proj, ".agent/loop.log"), errors="replace").read()
    since = log[log.find(f"iteration {first['iter']}:") - 20:] if f"iteration {first['iter']}:" in log else ""
    n_sync = since.count("PITFALLS.md synced")
    bad = re.findall(r"WARNING: (?:pitfalls-sync|decisions-archive) failed.*", since)
    big = re.findall(r"WARNING: DECISIONS.md is \d+ KB", since)
    say("FAIL" if bad else "PASS", f"loop log: {n_sync} PITFALLS.md syncs, {len(bad)} sync failures" + (f" ({bad[0][:80]})" if bad else ""))
    say("FAIL" if big else "PASS", f"loop log: {len(big)} DECISIONS.md 50 KB warnings since the cut-over")
    no_journal, wrong_task, rows = [], [], []
    for r in after:
        it, task = r["iter"], r.get("task") or ""
        tid = (re.match(r"tasks/(\d+[a-z]*)-", task) or [None, ""])[1]
        h = git(proj, "log", "-1", "--format=%H", f"--grep=before iteration {it})", "--", "PROGRESS.md").strip() or \
            git(proj, "log", "-1", "--format=%H", f"--before={r['start']}", "--", "PROGRESS.md").strip()   # unchanged: no commit
        prog = git(proj, "show", f"{h}:PROGRESS.md") if h else ""
        journal = prog.split("## Your task's journal", 1)
        if len(journal) < 2:
            no_journal.append(it)
        else:
            ids = re.findall(r"(?m)^### \d{4}-\d\d-\d\d (\d+[a-z]*)\b", journal[1].split("## Project-wide", 1)[0])
            if any(not (i == tid or tid.startswith(i)) for i in ids):
                wrong_task.append(it)
        f = os.path.join(proj, f".agent/sessions/iter-{it:04d}.jsonl")
        if os.path.exists(f):
            rows.append((it, task) + reads(f, task))
    say("FAIL" if no_journal else "PASS", f"PROGRESS.md carried the task's journal in {len(after) - len(no_journal)}/{len(after)} sessions"
        + (f" (missing: {no_journal[:8]})" if no_journal else ""))
    say("FAIL" if wrong_task else "PASS", f"journal entries all belonged to the session's task" + (f" (not in {wrong_task[:8]})" if wrong_task else ""))
    read_toc = sum(1 for r in rows if r[2])
    say("PASS" if read_toc == len(rows) else "WARN", f"contents block read in {read_toc}/{len(rows)} sessions")
    broke = [(it, b) for it, _, _, b, _, _ in rows if b]
    late = sum(len(r[5]) for r in rows)
    say("PASS", f"whole reads after spawn (usually before an edit of that file): {late} in {len(rows)} sessions")
    say("WARN" if broke else "PASS", f"sessions reading at spawn whole what they should search: {len(broke)}/{len(rows)}"
        + "".join(f"\n       iteration {it}: {', '.join(b)}" for it, b in broke[:10]))
    say("PASS", f"searches of DECISIONS/PITFALLS/PLAN: {sum(r[4] for r in rows)} in {len(rows)} sessions")
    cut = first["start"]
    diff = git(proj, "log", f"--since={cut}", "-p", "--format=@@C %H", "--", "PITFALLS.md", "DECISIONS.md")
    new, keyed, old_habit, cur = 0, 0, 0, None
    for line in diff.split("\n") + ["@@C end"]:
        if cur is not None and (not line.startswith("+") or line.startswith("+#### ") or line.startswith("+## ")):
            new += 1
            keyed += bool(re.search(r"(?m)^\s*Where:", cur) and re.search(r"(?m)^\s*Symptom:", cur))
            cur = None
        if line.startswith("+#### ") and not re.search(r"\b(PITFALL|LESSON)\b", line):
            cur = ""
        elif line.startswith("+## ") and re.search(r"\b(PITFALL|LESSON)\b", line):
            old_habit += 1
        elif cur is not None:
            cur += line[1:] + "\n"
    say("PASS" if new == keyed else "WARN", f"new PITFALLS.md entries since the cut-over: {new}, with both keys: {keyed}")
    say("PASS" if not old_habit else "WARN", f"PITFALLs still written to DECISIONS.md (moved by the driver): {old_habit}")


def main():
    for proj in sys.argv[1:]:
        print(f"== {proj}")
        if not os.path.exists(SYNC):
            say("FAIL", f"{SYNC} is not installed")
        files(proj)
        if os.path.exists(os.path.join(proj, ".agent/iterations.jsonl")):
            logs(proj)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
