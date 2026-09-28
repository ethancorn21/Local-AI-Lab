#!/usr/bin/env python3
"""ab-analyze.py : hand-over A/B results. Prints per-arm numbers and writes blind resume pairs for grading.

For every session after the first: the notes the previous session left (arm A: PROGRESS.md; arm B: the task file's
## Hand-over plus, for a subtask, the parent's) and what the next session actually did first (first tool calls and
first text). Pairs go to ~/ab/pairs.json shuffled, labelled only by a random id; the key goes to ~/ab/pairs-key.json.
"""
import json, os, random, re, subprocess, statistics as st

HOME = os.path.expanduser("~")
MEM = re.compile(r"(PROGRESS|DECISIONS|CODEMAP|AGENTS)\.md|tasks/")


def git(d, *a):
    return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True).stdout


def commit_at(d, ts):
    return git(d, "rev-list", "-1", f"--before={ts}", "HEAD").strip()


def handover(d, commit, task):
    t = git(d, "show", f"{commit}:{task}")
    m = re.search(r"(?ms)^## Hand-over\n(.*)\Z", t)
    return m.group(1).strip() if m else ""


def first_actions(d, it, n=8):
    f = os.path.join(d, f".agent/sessions/iter-{it:04d}.jsonl")
    acts, text, ctx, first_edit = [], "", 0, None
    if not os.path.exists(f) or os.path.getsize(f) == 0:
        return acts, text, first_edit
    for line in open(f):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") not in ("message", "message_end"):
            continue
        m = e.get("message") or {}
        if m.get("role") != "assistant":
            continue
        ctx = (m.get("usage") or {}).get("totalTokens") or ctx
        for c in m.get("content") or []:
            if c.get("type") == "text" and not text and c.get("text", "").strip():
                text = c["text"].strip()[:600]
            if c.get("type") == "toolCall":
                a = c.get("arguments") or {}
                p = str(a.get("path") or "")
                if c.get("name") in ("edit", "write") and not MEM.search(p) and first_edit is None:
                    first_edit = ctx
                if len(acts) < n:
                    what = p or str(a.get("command") or a.get("query") or a.get("url") or "")
                    acts.append(f"{c.get('name')}: {what[:160]}")
    return acts, text, first_edit


def arm(name):
    d = os.path.join(HOME, "ab", name, "hollowdeep")
    led = [json.loads(l) for l in open(os.path.join(d, ".agent/iterations.jsonl")) if l.strip()]
    rows, pairs = [], []
    for i, r in enumerate(led):
        acts, text, fe = first_actions(d, r["iter"])
        rows.append(dict(r, first_edit=fe))
        if i == 0:
            continue
        prev = led[i - 1]
        c = commit_at(d, prev["end"])
        if name == "A":
            notes = git(d, "show", f"{c}:PROGRESS.md").strip()
        else:
            notes = handover(d, c, r["task"])
            m = re.match(r"tasks/(\d{3}[a-z]*?)[a-z]-", r["task"])
            if m:
                par = [p for p in git(d, "ls-tree", "--name-only", c, "tasks/").split() if re.match(rf"tasks/{m.group(1)}-", p)]
                if par:
                    notes = (notes + "\n\n[parent " + par[0] + " hand-over]\n" + handover(d, c, par[0])).strip()
        pairs.append(dict(arm=name, iter=r["iter"], task=r["task"], prev_task=prev["task"],
                          notes=notes[-4000:], next_actions=acts, next_text=text))
    return rows, pairs


def summary(name, rows):
    n = len(rows)
    print(f"== arm {name}: {n} sessions")
    print(f"  tasks: " + ", ".join(f"{r['task'].replace('tasks/', '')[:5]}:{(r.get('verify') or r.get('status_after') or '')[:8]}" for r in rows))
    acc = sum(r.get("verify") == "accepted" for r in rows); rej = sum(r.get("verify") == "rejected" for r in rows)
    print(f"  accepted {acc}, rejected {rej}, handed over by harness {sum(bool(r.get('wrapup')) for r in rows)}, "
          f"median minutes {st.median([(r.get('agent_secs') or 0) / 60 for r in rows]):.1f}, "
          f"total hours {sum(r.get('agent_secs') or 0 for r in rows) / 3600:.2f}")
    fe = [r["first_edit"] for r in rows if r.get("first_edit")]
    print(f"  median context at first code edit: {st.median(fe) / 1000:.0f}k" if fe else "  no code edits")
    if name == "B":
        inn = sum(r.get("handover_in_task") is True for r in rows); g = sum(r.get("handover_guard") is True for r in rows)
        clean = sum(r.get("handover_in_task") is True and r.get("handover_guard") is not True for r in rows)
        print(f"  COMPLIANCE: hand-over written in the task file {inn}/{n}, cleanly (no PROGRESS.md write) {clean}/{n}, "
              f"safety net needed {g}/{n}")


if __name__ == "__main__":
    allpairs = []
    for name in ("A", "B"):
        try:
            rows, pairs = arm(name)
        except FileNotFoundError:
            print(f"== arm {name}: no ledger yet"); continue
        summary(name, rows); allpairs += pairs
    random.seed(7); random.shuffle(allpairs)
    key = {}
    for k, p in enumerate(allpairs):
        pid = f"P{k + 1:02d}"; key[pid] = dict(arm=p.pop("arm"), iter=p["iter"]); p["id"] = pid
    json.dump(allpairs, open(os.path.join(HOME, "ab", "pairs.json"), "w"), indent=1)
    json.dump(key, open(os.path.join(HOME, "ab", "pairs-key.json"), "w"), indent=1)
    print(f"\n{len(allpairs)} resume pairs written to ~/ab/pairs.json (key in pairs-key.json)")
