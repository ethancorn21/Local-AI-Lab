"""Effort A/B v2 judge (runs on the Mac, through headless `claude -p` on the subscription; no API key needed).

Judgments, all blind to effort (the model sees anonymous packets; only this script holds the key):
  spec   per run: did the developer notice each planted spec flaw, and how did it handle it (asked / documented an
         assumption / proposed a change / silent)? Other concerns it raised, and whether they were legitimate.
         Controls have no planted flaw, so every concern there is scored for legitimacy (false-alarm rate).
  code   per run: rubric 1-5 on six dimensions plus an overall 1-10, relative to the spec, not to size.
  pair   rep-matched xhigh vs medium (every probe and the project), shown as A/B in random order and judged twice
         with the order swapped, so a first-position bias cancels out.
Accuracy checks: `calibrate` grades synthetic packets with a known right answer and must pass before `run`;
every spec verdict's evidence quote is checked against the packet (invented evidence is flagged); `retest`
re-judges a seeded 20% of packets to measure the judge's own consistency; the report gives Wilson 95% intervals.
Judge: claude-opus-5-5[1m] (1M context, so no packet is ever trimmed), --effort high, its own system prompt,
--setting-sources project from an empty directory (no user settings, hooks, plugins, CLAUDE.md or memory), no tools.
Token usage of every call is saved with its output.

Usage:  judge.py pull                  copy packets + key from the VM into hidden/judge/ and scan them for leaks
        judge.py calibrate             known-answer check of the judge (run first; stops on a miss)
        judge.py run [spec|code|pair|all]   (resumable: skips judgments already saved)
        judge.py retest                second opinion on a seeded 20% of spec and code packets
        judge.py report                unblind, write hidden/judge/report.md
"""
import json
import math
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
MODEL = os.environ.get("JUDGE_MODEL", "claude-opus-5-5[1m]")
EFFORT = os.environ.get("JUDGE_EFFORT", "high")
SYSTEM = ("You are an impartial senior reviewer grading work for a controlled experiment. You never see who or what "
          "produced the work. Judge only what is in front of you, cite short evidence copied exactly from the material, "
          "and reply with only the JSON object asked for.")
DIMS = ("correctness_risk", "readability", "design", "tests", "robustness", "simplicity")


def claude(prompt):
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run(["claude", "-p", "--setting-sources", "project", "--strict-mcp-config", "--tools", "",
                            "--no-session-persistence", "--output-format", "json", "--model", MODEL, "--effort", EFFORT,
                            "--system-prompt", SYSTEM], input=prompt, capture_output=True, text=True, cwd=cwd, timeout=3600)
    try:
        d = json.loads(p.stdout)
        if d.get("api_error_status") == 429:   # subscription limit: stop instead of failing every remaining call; rerun resumes
            sys.exit(f"RATE_LIMITED: {d.get('result')}")
        out = json.loads(re.search(r"\{.*\}", d["result"], re.S).group(0))
        out["_meta"] = {"usage": d.get("usage"), "cost_usd_equiv": d.get("total_cost_usd"), "model": list((d.get("modelUsage") or {}).keys())}
        return out
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
  (a lucky or silent choice, not detection); record that under "handling".
- "handling": one of "asked" (sent a request about it), "documented_assumption" (wrote down the problem and the choice
  it made), "proposed_change" (wrote a proposed change to the spec), "silent_correct" (no words about it, but its
  behaviour matches the resolution given), "silent_other" (no words about it, other behaviour), "unclear".
- "quote": for "yes" or "partial", a short passage (under 30 words) copied EXACTLY, character for character, from the
  material below, with nothing added (no file name, no quotation marks around it); otherwise "".
- "where": where that passage appears (file or section), or "".

Then list OTHER concerns the developer raised about the spec (requests, notes, comments): for each, a one-line
summary, whether it is "legitimate" (a real gap, contradiction or risk in the spec) or not (the spec already answers
it, or it is not a problem), the channel ("request", "notes", "code_comment"), and an exact quote.

Reply with only JSON:
{{"flaws": [{{"id": "...", "detected": "yes|partial|no", "handling": "...", "quote": "...", "where": "..."}}],
  "other_concerns": [{{"summary": "...", "legitimate": true, "channel": "...", "quote": "..."}}], "notes": "one or two sentences"}}

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

PAIR_PROMPT = """Two developers built the same thing from the same specification (above). Compare their final code and
tests. Do not reward length or extra features; judge correctness against the spec, clarity, design and test quality.

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
SPLIT = "# The developer's code and tests (final state)"


# ---------- building prompts ----------
def spec_prompt(task, packet):
    key = answers(task)
    flaws = "\n".join(f"- {f['id']} ({f['type']}, {f['where']}): {f['description']} The author's resolution, "
                      f"if asked: {f['answer']}" for f in key["flaws"]) or "- none (this specification has no planted problem)"
    rule = "; ".join(f"{f['id']}: {f['detected_if']}" for f in key["flaws"]) or "n/a"
    return SPEC_PROMPT.format(flaws=flaws, detect_rule=rule, packet=packet)


def pair_prompt(code_a, code_b):
    spec = code_a.split(SPLIT, 1)[0]
    return spec + PAIR_PROMPT.format(a=code_a.split(SPLIT, 1)[-1], b=code_b.split(SPLIT, 1)[-1])


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[`*\"'“”‘’]", "", s)).strip().lower()


def verify_evidence(result, packet):
    """Mark each quote as found or not in the packet. If the judge wrapped the passage in quotation marks next to a
    location anyway, only the quoted parts are checked; fragments split on '...' must each appear."""
    text = norm(packet)
    for item in result.get("flaws", []) + result.get("other_concerns", []):
        q = item.get("quote") or item.get("evidence")
        if not q:
            continue
        quoted = re.findall(r'"([^"]{12,})"|“([^”]{12,})”', q)
        if quoted:
            q = " ... ".join(a or b for a, b in quoted)
        frags = [norm(f) for f in re.split(r"\.\.\.|…", q) if len(norm(f)) >= 12]
        item["evidence_found"] = bool(frags) and all(f in text for f in frags)
    return result


def save(path, obj):
    """Failed calls are not saved, so a rerun retries them (errors go to hidden/judge/errors.log)."""
    if "error" in obj:
        os.makedirs(J, exist_ok=True)
        with open(f"{J}/errors.log", "a") as f:
            f.write(json.dumps({"path": path, **{k: obj[k] for k in ("error", "raw") if k in obj}})[:2000] + "\n")
        print(f"ERROR {os.path.basename(path)}: {obj['error']}", flush=True)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w"), indent=1)


# ---------- commands ----------
def pull():
    os.makedirs(J, exist_ok=True)
    data = subprocess.run(["ssh", "harnessvm", "sudo tar -C /home/claude/ab-effort2-kit -czf - packets results/summary.json"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-C", J, "-xzf", "-"], input=data, check=True)
    files = [f for f in os.listdir(f"{J}/packets") if f.endswith(".md")]
    print(f"{len(files)} packet files in {J}/packets")
    for f in sorted(files):   # blinding check: effort words must not reach the judge
        t = open(f"{J}/packets/{f}").read()
        hits = re.findall(r".{0,50}\b(?:xhigh|THINKING|--thinking)\b.{0,50}", t)
        med = re.findall(r".{0,40}\bmedium\b.{0,40}", t)
        if hits:
            print(f"LEAK? {f}: {hits[:3]}")
        if med:
            print(f"check 'medium' in {f}: {med[:3]}")


def pairs():
    key = json.load(open(f"{J}/packets/key.json"))
    groups = defaultdict(dict)
    for anon, k in key.items():
        groups[(k["name"], k["rep"])][k["effort"]] = anon
    rng = random.Random(77)
    out = []
    for (name, rep), d in sorted(groups.items()):
        if "xhigh" in d and "medium" in d:
            p = [d["xhigh"], d["medium"]]
            rng.shuffle(p)
            out.append((name, rep, p))
    return out


def run(which="all", sub=None, outdir="out"):
    tasks = json.load(open(f"{J}/packets/tasks.json"))
    for anon, t in sorted(tasks.items()):
        if sub is not None and anon not in sub:
            continue
        if which in ("spec", "all") and not os.path.exists(f"{J}/{outdir}/{anon}.spec.json"):
            packet = open(f"{J}/packets/{anon}.spec.md").read()
            save(f"{J}/{outdir}/{anon}.spec.json", verify_evidence(claude(spec_prompt(t["name"], packet)), packet))
            print(f"spec {anon} done", flush=True)
        if which in ("code", "all") and not os.path.exists(f"{J}/{outdir}/{anon}.code.json"):
            save(f"{J}/{outdir}/{anon}.code.json", claude(CODE_PROMPT.format(packet=open(f"{J}/packets/{anon}.code.md").read())))
            print(f"code {anon} done", flush=True)
    if which in ("pair", "all") and sub is None:
        for name, rep, p in pairs():
            for order in (p, p[::-1]):
                path = f"{J}/{outdir}/pair-{name}-rep{rep}-{order[0]}-{order[1]}.json"
                if os.path.exists(path):
                    continue
                a, b = (open(f"{J}/packets/{x}.code.md").read() for x in order)
                save(path, {"A": order[0], "B": order[1], "task": name, "rep": rep, **claude(pair_prompt(a, b))})
                print(f"pair {name} rep{rep} {order} done", flush=True)


def retest():
    tasks = sorted(json.load(open(f"{J}/packets/tasks.json")))
    sub = set(random.Random(99).sample(tasks, max(1, round(0.2 * len(tasks)))))
    run("all", sub=sub, outdir="retest")


def calibrate():
    sys.path.insert(0, f"{HERE}/hidden")
    import calibration as C
    misses = 0
    for case in C.SPEC_CASES:
        r = verify_evidence(claude(spec_prompt(case["task"], case["packet"])), case["packet"])
        save(f"{J}/calibration/{case['name']}.json", r)
        got = {f.get("id"): (f.get("detected"), f.get("handling"), f.get("evidence_found")) for f in r.get("flaws", [])}
        ok = True
        for fid, (det, hand) in case["want_flaws"].items():
            g = got.get(fid, (None, None, None))
            if g[0] != det or (hand and g[1] not in hand) or (det in ("yes", "partial") and g[2] is False):
                ok = False
        if "want_false_concerns" in case:
            falses = sum(not o.get("legitimate") for o in r.get("other_concerns", []))
            ok = ok and falses >= case["want_false_concerns"]
        misses += not ok
        print(f"{'ok ' if ok else 'MISS'} spec {case['name']}: got {got} concerns {[(o.get('summary'), o.get('legitimate')) for o in r.get('other_concerns', [])]}")
    for case in C.CODE_CASES:
        good, bad = case["good"], case["bad"]
        rg, rb = claude(CODE_PROMPT.format(packet=good)), claude(CODE_PROMPT.format(packet=bad))
        p1, p2 = claude(pair_prompt(good, bad)), claude(pair_prompt(bad, good))
        save(f"{J}/calibration/{case['name']}.json", {"good": rg, "bad": rb, "pair_good_first": p1, "pair_bad_first": p2})
        ok = (rg.get("correctness_risk", 0) > rb.get("correctness_risk", 9) and rg.get("tests", 0) > rb.get("tests", 9)
              and p1.get("overall") == "A" and p2.get("overall") == "B")
        misses += not ok
        print(f"{'ok ' if ok else 'MISS'} code {case['name']}: good {rg.get('correctness_risk')}/{rg.get('tests')}/{rg.get('overall')} "
              f"bad {rb.get('correctness_risk')}/{rb.get('tests')}/{rb.get('overall')} pair {p1.get('overall')},{p2.get('overall')} (want A,B)")
    print(f"calibration: {'PASSED' if not misses else f'{misses} MISS(ES) - fix the prompts before judging'}")
    open(f"{J}/calibration/RESULT", "w").write("passed\n" if not misses else f"{misses} misses\n")
    return misses


def wilson(k, n, z=1.96):
    if not n:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 2), round(c + h, 2))


def binom_two_sided(k, n):
    if not n:
        return float("nan")
    pk = [math.comb(n, i) / 2 ** n for i in range(n + 1)]
    return round(min(1.0, sum(x for x in pk if x <= pk[k] + 1e-12)), 3)


def report():
    key = json.load(open(f"{J}/packets/key.json"))
    by = defaultdict(lambda: defaultdict(list))
    ev_total = ev_found = 0
    usage = Counter()
    L = ["# Effort A/B v2: judged results (blind judge, unblinded here)", ""]
    if os.path.exists(f"{J}/calibration/RESULT"):
        L += [f"Judge calibration: {open(f'{J}/calibration/RESULT').read().strip()}", ""]
    for f in os.listdir(f"{J}/out"):
        m = json.load(open(f"{J}/out/{f}")).get("_meta", {}).get("usage") or {}
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            usage[k] += m.get(k) or 0
    for anon, k in key.items():
        eff = k["effort"]
        s = json.load(open(f"{J}/out/{anon}.spec.json")) if os.path.exists(f"{J}/out/{anon}.spec.json") else {}
        c = json.load(open(f"{J}/out/{anon}.code.json")) if os.path.exists(f"{J}/out/{anon}.code.json") else {}
        for fl in s.get("flaws", []):
            det = fl.get("detected") == "yes" and fl.get("evidence_found", True) is not False
            by[eff]["detected"].append(det)
            by[eff][f"handling:{fl.get('handling')}"].append(1)
            by[eff][f"flaw:{k['name']}:{fl.get('id')}"].append("yes" if det else fl.get("detected"))
            if fl.get("detected") in ("yes", "partial"):
                ev_total += 1
                ev_found += fl.get("evidence_found") is True
        oc = s.get("other_concerns", [])
        by[eff]["concerns_legit"].append(sum(bool(o.get("legitimate")) for o in oc))
        by[eff]["concerns_false"].append(sum(not o.get("legitimate") for o in oc))
        if k["name"].startswith("c_"):
            by[eff]["control_runs"].append(1)
            by[eff]["control_false_alarms"].append(sum(not o.get("legitimate") for o in oc))
        for dim in DIMS + ("overall",):
            if isinstance(c.get(dim), (int, float)):
                by[eff][f"code:{k['kind']}:{dim}"].append(c[dim])
    for eff in ("xhigh", "medium"):
        d = by[eff]
        k_, n_ = sum(d["detected"]), len(d["detected"])
        L += [f"## {eff}", "",
              f"- planted flaws detected: {k_}/{n_} (95% CI {wilson(k_, n_)})",
              "- handling: " + ", ".join(f"{k.split(':', 1)[1]} {len(v)}" for k, v in sorted(d.items()) if k.startswith("handling:")),
              f"- other concerns: legitimate {sum(d['concerns_legit'])}, not legitimate {sum(d['concerns_false'])}; "
              f"false alarms on clean controls: {sum(d['control_false_alarms'])} in {len(d['control_runs'])} runs",
              "- per flaw: " + "; ".join(f"{k.split(':', 1)[1]} {dict(Counter(v))}" for k, v in sorted(d.items()) if k.startswith("flaw:")),
              "- code rubric means: " + ", ".join(f"{k.split(':', 1)[1]} {statistics.mean(v):.2f} (n={len(v)})" for k, v in sorted(d.items()) if k.startswith("code:")), ""]
    # Both orders of a pair are one comparison, not two independent ones: each pair's winner is the effort that won
    # more of its two verdicts (a 1-1 split or two ties is a tie), and the sign test runs over pairs.
    verdicts, net = defaultdict(Counter), defaultdict(lambda: defaultdict(int))
    for f in sorted(os.listdir(f"{J}/out")):
        if f.startswith("pair-"):
            p = json.load(open(f"{J}/out/{f}"))
            kind = "project" if p.get("task") == "authwatch" else "probes"
            for dim in DIMS + ("overall",):
                v = p.get(dim)
                e = key[p[v]]["effort"] if v in ("A", "B") else "tie"
                verdicts[(kind, dim)][e] += 1
                net[(kind, dim)][(p["task"], p["rep"])] += {"xhigh": 1, "medium": -1}.get(e, 0)
    if verdicts:
        L += ["## Pairwise (rep-matched, each pair judged in both orders)", "",
              "Pair winner = the effort that won more of the pair's two verdicts; p is a two-sided sign test over pairs.", "",
              "| scope | dimension | pairs won: xhigh | medium | tie/split | p | verdicts xhigh / medium / tie |", "|---|---|---|---|---|---|---|"]
        for (kind, dim), w in sorted(verdicts.items()):
            c = Counter("xhigh" if x > 0 else "medium" if x < 0 else "tie" for x in net[(kind, dim)].values())
            L.append(f"| {kind} | {dim} | {c['xhigh']} | {c['medium']} | {c['tie']} | "
                     f"{binom_two_sided(min(c['xhigh'], c['medium']), c['xhigh'] + c['medium'])} | {w['xhigh']} / {w['medium']} / {w['tie']} |")
        L.append("")
    if os.path.exists(f"{J}/results/summary.json"):   # flaw BEHAVIOUR comes from the hidden checks, not the judge
        rows = {r["id"]: r for r in json.load(open(f"{J}/results/summary.json"))["rows"]}
        beh = defaultdict(lambda: defaultdict(list))
        for anon, k in key.items():
            r = rows.get(k["id"])
            if not r:
                continue
            for fid, v in (r.get("flaw") or {}).items():
                ok = v.get("matches_answer") if isinstance(v, dict) else (v == 1.0)
                beh[k["effort"]][f"{k['name']}:{fid}"].append(bool(ok))
        L += ["## Flaw behaviour from the hidden checks (matches the author's resolution)", "",
              "| flaw | xhigh | medium |", "|---|---|---|"]
        for f in sorted({f for e in beh.values() for f in e}):
            cell = lambda e: f"{sum(beh[e][f])}/{len(beh[e][f])}" if beh[e][f] else "-"
            L.append(f"| {f} | {cell('xhigh')} | {cell('medium')} |")
        L.append("")
    if os.path.isdir(f"{J}/retest"):
        same = n = 0
        diffs = []
        for f in os.listdir(f"{J}/retest"):
            a, b = json.load(open(f"{J}/retest/{f}")), json.load(open(f"{J}/out/{f}")) if os.path.exists(f"{J}/out/{f}") else {}
            if f.endswith(".spec.json"):
                ga = {x.get("id"): x.get("detected") for x in a.get("flaws", [])}
                gb = {x.get("id"): x.get("detected") for x in b.get("flaws", [])}
                for i in ga:
                    n += 1
                    same += ga[i] == gb.get(i)
            elif f.endswith(".code.json") and isinstance(a.get("overall"), (int, float)) and isinstance(b.get("overall"), (int, float)):
                diffs.append(abs(a["overall"] - b["overall"]))
        L += ["## Judge consistency (seeded 20% judged twice)", "",
              f"- spec 'detected' verdict identical: {same}/{n}",
              f"- code overall score, mean absolute difference: {statistics.mean(diffs):.2f} over {len(diffs)} packets" if diffs else "- code: no retest data", ""]
    L += [f"Evidence quotes verified in the packet: {ev_found}/{ev_total} (a 'yes' whose quote is not found counts as not detected)", "",
          f"Judge token usage: {dict(usage)}"]
    open(f"{J}/report.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "run" and not (os.path.exists(f"{J}/calibration/RESULT") and open(f"{J}/calibration/RESULT").read().startswith("passed")):
        sys.exit("run `judge.py calibrate` first; it has to pass before the real judging")
    {"pull": pull, "calibrate": lambda: sys.exit(1 if calibrate() else 0), "retest": retest, "report": report,
     "run": lambda: run(sys.argv[2] if len(sys.argv) > 2 else "all")}[cmd]()
