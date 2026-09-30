"""Effort A/B v2: per-run metrics, per-effort summary, and blinded packets for the Claude judge.

Run on the harness VM:  sudo python3 analyze.py [--packets] [--mutate]   (--mutate: after all GPU runs; it is slow)
Reads KIT/schedule.json, KIT/results/<id>/ (meta, grade, responder log, vllm samples, loop log, ledger) and the run's
project directory (sessions, tasks, git). Writes KIT/results/summary.json and summary.md; with --packets also
KIT/packets/<anon>.{spec,code}.md plus KIT/packets/key.json (anon id -> run id, effort; keep it away from the judge).
Thinking share is by characters (thinking text vs visible text + tool arguments); "think tok" = output tokens x that share.
"""
import glob
import json
import os
import random
import re
import statistics
import subprocess
import sys

KIT = "/home/claude/ab-effort2-kit"
MEMORY = re.compile(r"^(AGENTS|CODEMAP|DECISIONS(-archive)?|PROGRESS|PLAN|GOAL(-CHECK)?)\.md$|^tasks/|^\.agent/|^codemap/")
TEXT_EXT = (".py", ".md", ".toml", ".cfg", ".ini", ".txt", ".json", ".service", ".timer", ".sh")


def read(path, default=""):
    try:
        return open(path, errors="replace").read()
    except OSError:
        return default


def git(pdir, *args):
    return subprocess.run(["git", "-c", f"safe.directory={pdir}", "-C", pdir, *args], capture_output=True, text=True).stdout


def session_stats(pdir):
    turns = out_tok = think = text = 0
    for f in sorted(glob.glob(f"{pdir}/.agent/sessions/iter-*.jsonl")):
        for line in open(f, errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "message_end" or (d.get("message") or {}).get("role") != "assistant":
                continue
            m = d["message"]
            turns += 1
            out_tok += (m.get("usage") or {}).get("output", 0) or 0
            for c in m.get("content") or []:
                if c.get("type") == "thinking":
                    think += len(c.get("thinking") or "")
                elif c.get("type") in ("text", "toolCall"):
                    text += len(c.get("text") or "") + len(json.dumps(c.get("arguments") or {}))
    share = think / max(think + text, 1)
    return {"turns": turns, "output_tokens": out_tok, "thinking_share": round(share, 3), "thinking_tokens_est": round(out_tok * share)}


def run_metrics(r):
    res = f"{KIT}/results/{r['id']}"
    meta = json.loads(read(f"{res}/meta.json", "{}"))
    pdir = meta.get("dir", "")
    loop = read(f"{res}/loop.log")
    ledger = [json.loads(l) for l in read(f"{res}/iterations.jsonl").splitlines() if l.strip().startswith("{")]
    grade = json.loads(read(f"{res}/grade.json", "{}") or "{}")
    asks = [json.loads(l) for l in read(f"{res}/responder.jsonl").splitlines() if l.strip()]
    samples = [l.split(",") for l in read(f"{res}/vllm.csv").splitlines()[1:] if l.count(",") >= 4]
    running = [float(s[1]) for s in samples]
    tasks = sorted(os.path.basename(t) for t in glob.glob(f"{pdir}/tasks/[0-9]*.md"))
    human = {"001"} if r["kind"] == "probe" else {"000", "999"}
    agent_tasks = [t for t in tasks if t.split("-")[0] not in human]
    gc = re.search(r"rounds=(\d+)", read(f"{pdir}/.agent/goal-check"))
    row = {"id": r["id"], "phase": r["phase"], "name": r["name"], "effort": r["effort"], "rep": r["rep"],
           "wall_min": round(meta.get("wall_s", 0) / 60, 1), "hard_capped": meta.get("hard_capped"),
           "sessions": len(ledger), "agent_min": round(sum(x.get("agent_secs") or 0 for x in ledger) / 60, 1),
           **session_stats(pdir),
           "asks": len(asks), "asks_by_flaw": [a["classified"] for a in asks],
           "verified_done": len(re.findall(r"verified done", loop)), "rejected_claims": len(re.findall(r"REJECTED done claim", loop)),
           "stalls": len(re.findall(r"STALL:", loop)), "timeouts": sum(bool(x.get("timed_out")) for x in ledger),
           "session_errors": sum(bool(x.get("session_error")) for x in ledger),
           "tasks_total": len(tasks), "agent_tasks": len(agent_tasks), "goal_check_rounds": int(gc.group(1)) if gc else None,
           "contention_samples": sum(x > 1 for x in running), "samples": len(running),
           "grade": grade}
    if r["kind"] == "probe":
        a, b = (grade.get("core") or "0/1").split("/")
        row["core"] = round(int(a) / int(b), 3)
        row["flaw"] = grade.get("flaw", {})
    else:
        row["core"] = grade.get("core_score")
        row["flaw"] = grade.get("flaws", {})
    return row


def summarize(rows):
    out = {}
    for phase in ("probes", "project"):
        for eff in ("xhigh", "medium"):
            rs = [r for r in rows if r["phase"] == phase and r["effort"] == eff]
            if not rs:
                continue
            agg = lambda k: [r[k] for r in rs if isinstance(r.get(k), (int, float))]
            out[f"{phase}/{eff}"] = {
                "runs": len(rs),
                **{f"{k}_median": round(statistics.median(agg(k)), 3) if agg(k) else None
                   for k in ("wall_min", "sessions", "output_tokens", "thinking_tokens_est", "thinking_share", "core", "mutation", "ruff_per_100_loc", "py_loc")},
                **{f"{k}_total": sum(agg(k)) for k in ("asks", "rejected_claims", "stalls", "timeouts", "session_errors", "agent_tasks", "contention_samples")},
                "core_mean": round(statistics.mean(agg("core")), 3) if agg("core") else None}
    return out


def md(rows, summ):
    L = ["# Effort A/B v2 results", "", "## Per effort", "", "| phase/effort | runs | wall min (median) | sessions | output tok | think tok | think share | core (mean) | asks | rejected | stalls | agent tasks | GPU contention samples |", "|" + "---|" * 13]
    for k, s in summ.items():
        L.append(f"| {k} | {s['runs']} | {s['wall_min_median']} | {s['sessions_median']} | {s['output_tokens_median']} | {s['thinking_tokens_est_median']} | "
                 f"{s['thinking_share_median']} | {s['core_mean']} | {s['asks_total']} | {s['rejected_claims_total']} | {s['stalls_total']} | {s['agent_tasks_total']} | {s['contention_samples_total']} |")
    L += ["", "## Per run", "", "| id | name | effort | rep | wall min | sessions | output tok | think share | core | flaw outcome | asks (flaws) | rejected | stalls |", "|" + "---|" * 13]
    for r in rows:
        L.append(f"| {r['id']} | {r['name']} | {r['effort']} | {r['rep']} | {r['wall_min']} | {r['sessions']} | {r['output_tokens']} | {r['thinking_share']} | "
                 f"{r['core']} | {json.dumps(r['flaw'])[:120]} | {r['asks']} {r['asks_by_flaw']} | {r['rejected_claims']} | {r['stalls']} |")
    return "\n".join(L) + "\n"


# ---------- judge packets ----------
def product_files(pdir):
    files = [f for f in git(pdir, "ls-files").splitlines() if f.endswith(TEXT_EXT) and not MEMORY.search(f)]
    return files


def anonymize(text, r, pdir):
    text = text.replace(pdir, "<project>").replace(os.path.dirname(pdir), "<runs>").replace(r["id"], "<run>")
    return re.sub(r"(?i)THINKING[= ]\w+|--thinking \w+|\bxhigh\b", "<scrubbed>", text)


def spec_packet(r, pdir, res):
    spec = read(f"{KIT}/hidden/project/GOAL.md") if r["kind"] == "project" else \
        "".join(read(f) for f in sorted(glob.glob(f"{KIT}/hidden/probes/{r['name']}/seed/tasks/*.md")))
    parts = [f"# Specification given to the developer\n\n{spec}\n"]
    asks = [json.loads(l) for l in read(f"{res}/responder.jsonl").splitlines() if l.strip()]
    for a in asks:
        parts.append(f"## Request the developer sent to the author ({a['ask']})\n\n{a['request']}\n\n### Reply it got\n\n{a['answer']}\n")
    for name in ("PLAN.md", "DECISIONS.md", "DECISIONS-archive.md", "GOAL-CHECK.md", "README.md"):
        t = read(f"{pdir}/{name}")
        if t.strip():
            parts.append(f"## The developer's {name}\n\n{t[:20000]}\n")
    for t in sorted(glob.glob(f"{pdir}/tasks/*.md")):
        body = read(t)
        for sec in re.findall(r"(?ms)^## (?:Hand-over|Proposed changes).*?(?=^## |\Z)", body):
            parts.append(f"## From task file {os.path.basename(t)}\n\n{sec[:6000]}\n")
    msgs = [m for m in git(pdir, "log", "--format=%s%n%b%x00").split("\x00") if m.strip() and not m.strip().startswith("[driver]")]
    parts.append("## The developer's commit messages (oldest last)\n\n" + "\n---\n".join(m.strip()[:1500] for m in msgs[:80]) + "\n")
    if r["kind"] == "probe":
        for f in product_files(pdir):
            if not f.startswith("tests/fixtures"):
                parts.append(f"## File {f}\n\n```\n{read(os.path.join(pdir, f))[:15000]}\n```\n")
    else:
        notes = []
        for f in product_files(pdir):
            for i, line in enumerate(read(os.path.join(pdir, f)).splitlines(), 1):
                if re.search(r"#.*\b(assum|spec|example|contradict|ambigu|unclear|year|GOAL)", line, re.I):
                    notes.append(f"{f}:{i}: {line.strip()[:200]}")
        parts.append("## Code comments that mention the spec, assumptions, examples or the year\n\n" + "\n".join(notes[:150]) + "\n")
    return anonymize("\n".join(parts), r, pdir)


def code_packet(r, pdir):
    spec = read(f"{KIT}/hidden/project/GOAL.md") if r["kind"] == "project" else \
        "".join(read(f) for f in sorted(glob.glob(f"{KIT}/hidden/probes/{r['name']}/seed/tasks/*.md")))
    parts = [f"# Specification\n\n{spec}\n", "# The developer's code and tests (final state)\n"]
    total = 0
    for f in product_files(pdir):
        body = read(os.path.join(pdir, f))
        if f.startswith(("samples/", "tests/fixtures", "fixtures/")) and len(body) > 3000:
            body = body[:3000] + "\n[... truncated sample data ...]"
        parts.append(f"## {f}\n\n```\n{body[:40000]}\n```\n")
        total += len(body)
        if total > 400_000:
            parts.append("[... remaining files omitted: size limit ...]")
            break
    return anonymize("\n".join(parts), r, pdir)


def mutation(r, pdir, res):
    """Mutation score of the arm's own tests, on a copy, as agent (it runs the arm's code)."""
    if os.path.exists(f"{res}/mutation.json"):
        return json.loads(read(f"{res}/mutation.json"))
    n = 40 if r["kind"] == "project" else 30
    seed = int(r["id"].lstrip("r") or 0)
    tmp = subprocess.run(["sudo", "-u", "agent", "mktemp", "-d", "/tmp/abmut.XXXXXX"], capture_output=True, text=True).stdout.strip()
    try:
        subprocess.run(["sudo", "-u", "agent", "git", "clone", "-q", pdir, f"{tmp}/p"], check=True, capture_output=True)
        subprocess.run(["sudo", "-u", "agent", "tee", f"{tmp}/mutate.py"], input=read(os.path.join(os.path.dirname(__file__), "mutate.py")),
                       text=True, capture_output=True, check=True)
        p = subprocess.run(["sudo", "-u", "agent", "-H", "bash", "-c", f"cd {tmp} && timeout 14400 python3 mutate.py {tmp}/p {n} {seed}"],
                           capture_output=True, text=True)
        out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {"error": p.stderr[-300:]}
    except Exception as e:
        out = {"error": repr(e)[:300]}
    finally:
        subprocess.run(["sudo", "rm", "-rf", tmp], capture_output=True)
    json.dump(out, open(f"{res}/mutation.json", "w"), indent=1)
    return out


def lint(pdir, res):
    """ruff (default rules) issues per 100 product lines; static only, never runs the arm's code."""
    files = [os.path.join(pdir, f) for f in product_files(pdir) if f.endswith(".py")]
    loc = sum(len(read(f).splitlines()) for f in files)
    ruff = f"{KIT}/.venv/bin/ruff"
    issues = 0
    if files and os.path.exists(ruff):
        p = subprocess.run([ruff, "check", "--no-cache", "--output-format", "json", *files], capture_output=True, text=True)
        try:
            issues = len(json.loads(p.stdout or "[]"))
        except ValueError:
            issues = None
    test_loc = sum(len(read(f).splitlines()) for f in files if "/tests/" in f or os.path.basename(f).startswith("test_"))
    return {"py_loc": loc, "test_loc": test_loc, "ruff_issues": issues, "ruff_per_100_loc": round(100 * issues / loc, 2) if loc and issues is not None else None}


def main():
    sched = json.load(open(f"{KIT}/schedule.json"))
    done = [r for r in sched if read(f"{KIT}/results/{r['id']}/status").strip() == "done"]
    rows = [run_metrics(r) for r in done]
    for row, r in zip(rows, done):
        res = f"{KIT}/results/{r['id']}"
        pdir = json.loads(read(f"{res}/meta.json"))["dir"]
        row.update(lint(pdir, res))
        if "--mutate" in sys.argv or os.path.exists(f"{res}/mutation.json"):
            row["mutation"] = mutation(r, pdir, res).get("score")
    summ = summarize(rows)
    json.dump({"rows": rows, "summary": summ}, open(f"{KIT}/results/summary.json", "w"), indent=1)
    open(f"{KIT}/results/summary.md", "w").write(md(rows, summ))
    print(md(rows, summ))
    if "--packets" in sys.argv:
        os.makedirs(f"{KIT}/packets", exist_ok=True)
        ids = [f"S{i:03d}" for i in range(1, len(done) + 1)]
        random.Random(4242).shuffle(ids)
        key = {}
        for anon, r in zip(ids, done):
            pdir = json.loads(read(f"{KIT}/results/{r['id']}/meta.json"))["dir"]
            open(f"{KIT}/packets/{anon}.spec.md", "w").write(spec_packet(r, pdir, f"{KIT}/results/{r['id']}"))
            open(f"{KIT}/packets/{anon}.code.md", "w").write(code_packet(r, pdir))
            key[anon] = {"id": r["id"], "effort": r["effort"], "name": r["name"], "kind": r["kind"], "rep": r["rep"]}
        json.dump(key, open(f"{KIT}/packets/key.json", "w"), indent=1)
        json.dump({k: {"name": v["name"], "kind": v["kind"]} for k, v in key.items()}, open(f"{KIT}/packets/tasks.json", "w"), indent=1)
        print(f"{len(key)} packets in {KIT}/packets")


if __name__ == "__main__":
    main()
