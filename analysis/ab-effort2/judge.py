"""Effort A/B v2 judge (runs on the Mac, through headless `claude -p` on the subscription; no API key needed).

Three judgments, all blind to effort (the model sees anonymous packets; only this script knows the key):
  spec   per run: did the developer notice each planted spec flaw, and how did it handle it (asked / documented an
         assumption / proposed a change / silent)? Which other concerns did it raise, and were they legitimate?
         Controls have no planted flaw, so every concern there is scored for legitimacy (false-alarm rate).
  code   per run: rubric 1-5 on six dimensions plus an overall 1-10, relative to the spec, not to size.
  pair   project runs only: rep-matched xhigh vs medium, shown as A/B in random order, judged twice with the order
         swapped (a judge's first-position bias cancels out).
Isolation: --system-prompt replaces Claude Code's prompt, --setting-sources project from an empty directory drops
user settings, hooks and plugins, --tools "" removes tools, no CLAUDE.md or memory reaches the judge (checked).

Usage:  judge.py pull            copy packets + key from the VM into hidden/judge/
        judge.py run [spec|code|pair|all]   (resumable: skips judgments already saved)
        judge.py report          unblind and write hidden/judge/report.md
"""
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
J = f"{HERE}/hidden/judge"
MODEL = os.environ.get("JUDGE_MODEL", "claude-opus-5-5")
SYSTEM = ("You are an impartial senior reviewer grading work for a controlled experiment. You never see who or what "
          "produced the work. Judge only what is in front of you, cite short evidence, and reply with only the JSON "
          "object asked for.")


def claude(prompt):
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run(["claude", "-p", "--setting-sources", "project", "--strict-mcp-config", "--tools", "",
                            "--no-session-persistence", "--output-format", "json", "--model", MODEL,
                            "--system-prompt", SYSTEM], input=prompt, capture_output=True, text=True, cwd=cwd, timeout=1800)
    try:
        text = json.loads(p.stdout)["result"]
        return json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except Exception as e:
        return {"error": f"{e!r}"[:200], "raw": (p.stdout or p.stderr)[-1500:]}


def answers(task):
    path = f"{HERE}/hidden/project/answers.json" if task == "authwatch" else f"{HERE}/hidden/probes/{task}/answers.json"
    return json.load(open(path))


SPEC_PROMPT = """You are grading how a developer READ a specification: whether they noticed its problems and how they
handled them. Correctness of the code is graded elsewhere; here only spec reading counts.

Known planted problems in this specification (the developer was not told about them):
{flaws}

The developer could reach the spec's author with a request (you will see any requests and the replies they got), and
could write notes, plans, decisions, commit messages and code comments.

Grade each planted problem:
- "detected": "yes" if the developer's own words show they saw this specific problem ({detect_rule}); "partial" if
  they touched it without identifying the problem; "no" otherwise. Silently implementing the right behaviour is "no"
  (it is a lucky or silent choice, not detection); record that under "handling".
- "handling": one of "asked" (sent a request about it), "documented_assumption" (wrote down the problem and the choice
  it made), "proposed_change" (wrote a proposed change to the spec), "silent_correct" (no words about it, but its
  behaviour matches the resolution given), "silent_other" (no words about it, other behaviour), "unclear".
- "evidence": a short quote (max 30 words) with where it appears, or "".

Then list OTHER concerns the developer raised about the spec (requests, notes, comments): for each, a one-line
summary, whether it is "legitimate" (a real gap, contradiction or risk in the spec) or not (the spec already answers
it, or it is not a problem), and the channel ("request", "notes", "code_comment").

Reply with only JSON:
{{"flaws": [{{"id": "...", "detected": "yes|partial|no", "handling": "...", "evidence": "..."}}],
  "other_concerns": [{{"summary": "...", "legitimate": true, "channel": "..."}}], "notes": "one or two sentences"}}

=== PACKET ===
{packet}
"""

CODE_PROMPT = """Grade the quality of this developer's final code and tests against its specification. Do not reward
length or extra features: code that does what the spec asks, simply and clearly, beats code that does more. Do not
run anything; read it.

Score each dimension 1 (poor) to 5 (excellent):
- "correctness_risk": 5 = you see no likely bugs against the spec; 1 = clear bugs or unhandled required cases
- "readability": names, structure of functions, comments where they help, no dead code
- "design": fits the problem, sensible modules and boundaries, no needless abstraction or duplication
- "tests": do the tests pin the spec's behaviour, including edge cases and thresholds, rather than restating the code?
- "robustness": bad input, errors, edge cases the spec mentions
- "simplicity": the least machinery that does the job
Also "overall" 1-10, and "top_issues": up to 5 short, specific issues (file and what), worst first.

Reply with only JSON: {{"correctness_risk": n, "readability": n, "design": n, "tests": n, "robustness": n,
"simplicity": n, "overall": n, "top_issues": ["..."], "summary": "two sentences"}}

=== PACKET ===
{packet}
"""

PAIR_PROMPT = """Two developers built the same project from the same specification. Compare their final code and tests.
Do not reward length or extra features; judge correctness against the spec, clarity, design and test quality.

For each dimension answer "A", "B" or "tie": correctness_risk (which has fewer likely bugs), readability, design,
tests, robustness, simplicity, and "overall" (which would you rather maintain and trust in production). Add
"reasons": three short sentences.

Reply with only JSON: {{"correctness_risk": "...", "readability": "...", "design": "...", "tests": "...",
"robustness": "...", "simplicity": "...", "overall": "...", "reasons": "..."}}

=== IMPLEMENTATION A ===
{a}

=== IMPLEMENTATION B ===
{b}
"""


def pull():
    os.makedirs(J, exist_ok=True)
    data = subprocess.run(["ssh", "harnessvm", "sudo tar -C /home/claude/ab-effort2-kit -czf - packets"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-C", J, "-xzf", "-"], input=data, check=True)
    print(f"{len(os.listdir(f'{J}/packets'))} files in {J}/packets")


def load_tasks():
    return json.load(open(f"{J}/packets/tasks.json"))   # anon -> {"name", "kind"}: no effort in it


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w"), indent=1)


def run(which="all"):
    tasks = load_tasks()
    for anon, t in sorted(tasks.items()):
        if which in ("spec", "all") and not os.path.exists(f"{J}/out/{anon}.spec.json"):
            key = answers(t["name"])
            flaws = "\n".join(f"- {f['id']} ({f['type']}, {f['where']}): {f['description']} The author's resolution, "
                              f"if asked: {f['answer']}" for f in key["flaws"]) or "- none (this specification has no planted problem)"
            rule = "; ".join(f"{f['id']}: {f['detected_if']}" for f in key["flaws"]) or "n/a"
            packet = open(f"{J}/packets/{anon}.spec.md").read()
            save(f"{J}/out/{anon}.spec.json", claude(SPEC_PROMPT.format(flaws=flaws, detect_rule=rule, packet=packet)))
            print(f"spec {anon} done", flush=True)
        if which in ("code", "all") and not os.path.exists(f"{J}/out/{anon}.code.json"):
            save(f"{J}/out/{anon}.code.json", claude(CODE_PROMPT.format(packet=open(f"{J}/packets/{anon}.code.md").read())))
            print(f"code {anon} done", flush=True)
    if which in ("pair", "all"):
        key = json.load(open(f"{J}/packets/key.json"))
        proj = defaultdict(dict)
        for anon, k in key.items():
            if k["kind"] == "project":
                proj[k["rep"]][k["effort"]] = anon
        rng = random.Random(77)
        for rep, d in sorted(proj.items()):
            if len(d) < 2:
                continue
            pair = [d["xhigh"], d["medium"]]
            rng.shuffle(pair)
            for order in (pair, pair[::-1]):
                path = f"{J}/out/pair-rep{rep}-{order[0]}-{order[1]}.json"
                if os.path.exists(path):
                    continue
                strip = lambda anon: open(f"{J}/packets/{anon}.code.md").read().split("# The developer's code and tests (final state)", 1)[-1]
                spec = open(f"{J}/packets/{order[0]}.code.md").read().split("# The developer's code and tests", 1)[0]
                save(path, {"A": order[0], "B": order[1], **claude(spec + PAIR_PROMPT.format(a=strip(order[0]), b=strip(order[1])))})
                print(f"pair rep{rep} {order} done", flush=True)


def report():
    key = json.load(open(f"{J}/packets/key.json"))
    by = defaultdict(lambda: defaultdict(list))
    L = ["# Effort A/B v2: judged results (blind, unblinded here)", ""]
    for anon, k in key.items():
        eff = k["effort"]
        s = json.load(open(f"{J}/out/{anon}.spec.json")) if os.path.exists(f"{J}/out/{anon}.spec.json") else {}
        c = json.load(open(f"{J}/out/{anon}.code.json")) if os.path.exists(f"{J}/out/{anon}.code.json") else {}
        for f in s.get("flaws", []):
            by[eff]["detected"].append(f.get("detected") == "yes")
            by[eff][f"handling:{f.get('handling')}"].append(1)
            by[eff][f"flaw:{k['name']}:{f.get('id')}"].append(f.get("detected"))
        oc = s.get("other_concerns", [])
        by[eff]["concerns_legit"].append(sum(bool(o.get("legitimate")) for o in oc))
        by[eff]["concerns_false"].append(sum(not o.get("legitimate") for o in oc))
        if k["name"].startswith("c_"):
            by[eff]["control_false_alarms"].append(sum(not o.get("legitimate") for o in oc))
        for dim in ("correctness_risk", "readability", "design", "tests", "robustness", "simplicity", "overall"):
            if isinstance(c.get(dim), (int, float)):
                by[eff][f"code:{k['kind']}:{dim}"].append(c[dim])
    for eff in ("xhigh", "medium"):
        d = by[eff]
        L += [f"## {eff}", "",
              f"- planted flaws detected: {sum(d['detected'])}/{len(d['detected'])}",
              f"- handling: " + ", ".join(f"{k.split(':', 1)[1]} {len(v)}" for k, v in sorted(d.items()) if k.startswith("handling:")),
              f"- other concerns raised: legitimate {sum(d['concerns_legit'])}, not legitimate {sum(d['concerns_false'])}; "
              f"false alarms on the clean controls: {sum(d['control_false_alarms'])} over {len(d['control_false_alarms'])} runs",
              "- per flaw: " + "; ".join(f"{k.split(':', 1)[1]} {dict(Counter(v))}" for k, v in sorted(d.items()) if k.startswith("flaw:")),
              "- code rubric means: " + ", ".join(f"{k.split(':', 1)[1]} {statistics.mean(v):.2f} (n={len(v)})" for k, v in sorted(d.items()) if k.startswith("code:")), ""]
    wins = Counter()
    for f in sorted(os.listdir(f"{J}/out")):
        if f.startswith("pair-"):
            p = json.load(open(f"{J}/out/{f}"))
            for dim in ("overall", "correctness_risk", "tests", "design"):
                v = p.get(dim)
                wins[(dim, key[p[v]]["effort"] if v in ("A", "B") else "tie")] += 1
    if wins:
        L += ["## Pairwise (project, rep-matched, both orders)", ""] + [f"- {dim}: " + ", ".join(f"{e} {wins[(dim, e)]}" for e in ("xhigh", "medium", "tie"))
                                                                        for dim in ("overall", "correctness_risk", "tests", "design")]
    open(f"{J}/report.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"pull": pull, "run": lambda: run(sys.argv[2] if len(sys.argv) > 2 else "all"), "report": report}[cmd]()
