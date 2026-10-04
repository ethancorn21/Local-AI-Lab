#!/usr/bin/env python3
"""memory-layout-eval.py <project dir>... : sessions before vs after the PITFALLS.md memory layout (2026-10-04).

Not an A/B: the same agents on the same project, earlier tasks against later ones. The question is whether reading
less at spawn costs capability. Per agent, phase (before = the ledger has no PITFALLS.md size, after = it has) and
session kind (plan = 000, goal = 999, work = the rest):
  orient     context (k tokens) when the first tool call that is not a memory read is made, median
  peak       highest context in the session, median
  handover   share of sessions that hit the hand-over limit (wrapup steered or aborted)
  progress   share of sessions with an agent commit
  claims     done claims accepted / rejected by the driver
  errors     tool calls that failed, per session
  reads      memory-file bytes per session that came back from whole reads vs searches, by file
Then candidate re-discoveries: PITFALLs whose heading and body start share words with an earlier one. Word overlap
only shortlists (frontpage 2026-10-04: real duplicates scored 0.14-0.24, two different flaky-test notes 0.27), so each
pair is judged by reading it. Baseline judged: 3 real duplicates in the 215 work sessions before the change (the
Playwright single-value matcher pair, the read-tool re-wrapping pair, the acceptance-boxes-verbatim pair).
"""
import collections, glob, json, os, re, statistics, subprocess, sys

MEM = re.compile(r"(AGENTS|PROGRESS|PLAN|DECISIONS|CODEMAP|PITFALLS)\.md|tasks/|git log")
FILES = ("DECISIONS.md", "PLAN.md", "PITFALLS.md", "PROGRESS.md", "CODEMAP.md", "AGENTS.md")
SEARCH = re.compile(r"\b(grep|rg|awk)\b|sed -n '/|contents:end")
WORD = re.compile(r"[a-z0-9_.]{3,}")
THRESH = float(os.environ.get("REDISCOVERY_OVERLAP", "0.12"))   # word overlap only shortlists: judge each pair
STOP = set("the and for with that this not are was its but from has have when into only one two all any can".split())


def kind(task):
    tid = re.match(r"tasks/(\d+)", task or "")
    return {"000": "plan", "999": "goal"}.get(tid.group(1) if tid else "", "work")


def session(path):
    """-> dict of what one session log shows."""
    s = {"orient": None, "peak": 0, "errors": 0, "whole": collections.Counter(), "search": collections.Counter()}
    work, calls = False, {}
    for line in open(path, errors="replace"):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = e.get("type")
        if t == "tool_execution_start":
            a = e.get("args") or {}
            blob = json.dumps(a)
            calls[e.get("toolCallId")] = (e.get("toolName"), a, blob)
            if not work and not MEM.search(blob):
                work = True
        elif t == "tool_execution_end":
            name, a, blob = calls.pop(e.get("toolCallId"), (None, {}, ""))
            if e.get("isError"):
                s["errors"] += 1
            text = "".join(c.get("text", "") for c in ((e.get("result") or {}).get("content") or []) if isinstance(c, dict))
            for f in FILES:
                if f in blob:
                    searched = (name == "bash" and SEARCH.search(blob)) or (name == "read" and a.get("limit"))
                    s["search" if searched else "whole"][f] += len(text)
        elif t == "message_end" and (e.get("message") or {}).get("role") == "assistant":
            u = e["message"].get("usage") or {}
            c = (u.get("input") or 0) + (u.get("cacheRead") or 0)
            s["peak"] = max(s["peak"], c)
            if work and s["orient"] is None:
                s["orient"] = c
    return s


def med(xs):
    xs = [x for x in xs if x is not None]
    return f"{statistics.median(xs) / 1000:5.1f}k" if xs else "    -"


def pitfall_heads(proj):
    """-> [(date, words, heading)] for every PITFALL ever added, from git history: the heading and the start of its
    body (identifiers live there), each fact once (moved and re-merged entries reappear in diffs)."""
    log = subprocess.run(["git", "-C", proj, "log", "--reverse", "-p", "--format=@@C %cI", "--",
                          "DECISIONS.md", "PITFALLS.md"], capture_output=True, text=True).stdout
    seen, out, date, cur = set(), [], "", None
    for line in log.split("\n") + ["@@C end"]:
        head = re.match(r"^\+(## |#### )(.*)$", line) if not line.startswith("+++") else None
        if cur is not None and (head or not line.startswith("+") or line.startswith("@@C ")):
            key, body = cur
            words = {w for w in WORD.findall((key + " " + body[:500]).lower()) if w not in STOP}
            out.append((cur_date, words, key))
            cur = None
        if line.startswith("@@C "):
            date = line[4:]
            continue
        if head:
            text = head.group(2).strip()
            is_pit = re.search(r"\b(PITFALL|LESSON)\b", text) or head.group(1) == "#### "
            key = re.sub(r"^\d{4}-\d\d-\d\d \S+ (PITFALL|LESSON): ", "", text)
            if is_pit and key not in seen:
                seen.add(key)
                cur, cur_date = (key, ""), date
        elif cur is not None:
            cur = (cur[0], cur[1] + " " + line[1:])
    return out


def main():
    projs = sys.argv[1:]
    rows = collections.defaultdict(list)
    cutover = {}
    for proj in projs:
        agent = os.path.basename(proj.rstrip("/"))
        for line in open(os.path.join(proj, ".agent/iterations.jsonl")):
            r = json.loads(line)
            phase = "after" if "PITFALLS.md" in (r.get("memory_bytes") or {}) else "before"
            if phase == "after":
                cutover.setdefault(proj, r.get("start"))
            f = os.path.join(proj, f".agent/sessions/iter-{r['iter']:04d}.jsonl")
            s = session(f) if os.path.exists(f) else None
            rows[(agent, phase, kind(r.get("task")))].append((r, s))
    print(f"{'agent':12} {'phase':6} {'kind':4} {'n':>4} {'orient':>7} {'peak':>7} {'handover':>8} {'progress':>8} "
          f"{'claims ok/rej':>13} {'errors':>6}   reads per session (whole | search), KB")
    for (agent, phase, k), rs in sorted(rows.items()):
        ss = [s for _, s in rs if s]
        n = len(rs)
        ho = sum(1 for r, _ in rs if r.get("wrapup") in ("steered", "aborted")) / n
        pr = sum(1 for r, _ in rs if (r.get("agent_commits") or 0) > 0) / n
        acc = sum(1 for r, _ in rs if r.get("verify") == "accepted")
        rej = sum(1 for r, _ in rs if r.get("verify") == "rejected")
        err = sum(s["errors"] for s in ss) / max(1, len(ss))
        reads = " ".join(f"{f.split('.')[0][:4]} {sum(s['whole'][f] for s in ss) / max(1, len(ss)) / 1024:.0f}|"
                         f"{sum(s['search'][f] for s in ss) / max(1, len(ss)) / 1024:.0f}" for f in FILES[:3])
        print(f"{agent:12} {phase:6} {k:4} {n:>4} {med([s['orient'] for s in ss]):>7} {med([s['peak'] for s in ss]):>7} "
              f"{ho:>7.0%} {pr:>8.0%} {acc:>6}/{rej:<6} {err:>6.1f}   {reads}")
    # possible re-discoveries: a PITFALL whose heading shares most of its words with an earlier one
    first = {}   # the worktrees share history: each fact once, at its earliest date
    for p in projs:
        for d, w, k in pitfall_heads(p):
            if k not in first or d < first[k][0]:
                first[k] = (d, frozenset(w))
    heads = sorted((d, w, k) for k, (d, w) in first.items())
    print(f"\npossible re-discoveries (word overlap >= {THRESH} with an earlier PITFALL, heading and body start; review by hand):")
    cut = min(cutover.values()) if cutover else None
    count = collections.Counter()
    for i, (d, w, k) in enumerate(heads):
        best = max(((len(w & w2) / max(1, len(w | w2)), k2) for d2, w2, k2 in heads[:i]), default=(0, ""))
        if best[0] >= THRESH:
            phase = "after" if cut and d >= cut else "before"
            count[phase] += 1
            print(f"  [{phase}] {d[:10]} {k[:90]}\n           ~ {best[1][:90]} ({best[0]:.2f})")
    work = collections.Counter(p for (a, p, k), rs in rows.items() for _ in rs if k == "work")
    for phase in ("before", "after"):
        if work[phase]:
            print(f"  {phase}: {count[phase]} in {work[phase]} work sessions ({100 * count[phase] / work[phase]:.1f} per 100)")


if __name__ == "__main__":
    main()
