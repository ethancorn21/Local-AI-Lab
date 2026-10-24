#!/usr/bin/env python3
"""expert-profile.py: a Strata expert ranking learned from coding work, for the big setup's tiers.

Why (10-20, the owner: "did you test putting all of the coding experts on the 3090s, more experts on the 5060ti, lesser
used experts on ram, and then least used on nvme?"): the helper arms (fn-*-2h) already tier the experts that way, but
in the order of Strata's shipped profile, which ranks them for a document summary; on Strata's own measurements a code
prompt finds 60% of its routed experts in a cache filled from it, 85% for a summary (tools/make_profile_mix.py), and
the ranking decides what the 3090s' caches start with, what the 5060 Ti holds and what the 8 GiB RAM budget keeps.
The server can save what its adaptive tier learned (--expert-profile-save, #477: the experts in VRAM, then the routing
counted since the start); a server started from that file begins with a coding ranking instead.

  corpus --out FILE                 coding prompts that share nothing with the held-out set: 12 medium LiveCodeBench
                                    problems (the hard test uses 24 hard ones), Strata's own 12 code prompts, and 8
                                    practical tasks written for this
  warm --url URL --corpus FILE      send them one at a time with production sampling and thinking (budget LCB_BUDGET,
                                    default 3000 here), so the server's tier learns them
  compare A B                       how far two profiles agree: shared pairs in each top N
  io ENGINE_LOG                     the last start's tiers (VRAM, helper, RAM copy) and what each request read from the
                                    drive (Strata's "file tier I/O this request" lines; 10-20, the zero-NVMe goal)
"""
import argparse, importlib.util, json, os, struct, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
STRATA_CORPUS = "/opt/llm/engines/src/strata/bench/profile_corpus.json"

PRACTICAL = [
    "Write a Python program that reads a web server access log in Common Log Format from stdin and prints the 5 most "
    "requested paths with their counts, then the number of 5xx responses per hour, hours in order.",
    "Write a Python program that reads a config file with [sections] and dotted keys from stdin and prints every "
    "setting flattened as section.key=value, sorted, later duplicates overriding earlier ones; quoted values may "
    "contain escaped quotes and # characters that are not comments.",
    "Write a Python function that evaluates arithmetic expressions with variables, operator precedence, parentheses, "
    "unary minus and exponentiation (right-associative) using a recursive-descent parser, and reports syntax errors "
    "with the column where they occur. Read expressions and variable assignments from stdin.",
    "Write a Python function that parses a five-field cron expression (lists, ranges, steps, month and weekday names) "
    "and returns the next 5 times it fires after a given datetime, handling day-of-month and day-of-week the way "
    "Vixie cron does and months without the given day.",
    "Write a Python program that reads two texts from stdin separated by a line containing only '---' and prints a "
    "unified diff with 3 lines of context, using a longest-common-subsequence algorithm you write yourself (no "
    "difflib).",
    "Write a JSON pretty-printer in Python without the json module: parse the document from stdin, report the line "
    "and column of the first error if it is invalid, and otherwise print it with 2-space indentation and sorted keys, "
    "keeping every number exactly as written.",
    "Write a Python program that reads people's working hours with their UTC offsets and their busy intervals from "
    "stdin and prints the earliest 30-minute slot in the next 7 days when everyone is free and inside working hours.",
    "Write a Python program that reads postal addresses from stdin, normalizes them (case, whitespace, Street/St, "
    "Avenue/Ave, unit numbers written as Apt, Unit or #) and prints groups of addresses that are the same place, "
    "largest group first.",
]


def lcb():
    spec = importlib.util.spec_from_file_location("lcbq", os.path.join(HERE, "lcb-quick.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def corpus(a):
    os.environ.update(LCB_DATA=os.path.join(HERE, "lcb", "test6.jsonl"), LCB_N_HARD="0", LCB_N_MED="12")
    q = lcb()
    rows = [{"d": "lcb-medium", "p": q.PROMPT.format(q=r["question_content"])} for r in q.problems()]
    rows += [{"d": "strata-code", "p": c["p"]} for c in json.load(open(STRATA_CORPUS)) if c["d"] == "code"]
    rows += [{"d": "practical", "p": p} for p in PRACTICAL]
    json.dump(rows, open(a.out, "w"), indent=1)
    kinds = {}
    for r in rows:
        kinds[r["d"]] = kinds.get(r["d"], 0) + 1
    print(f"{a.out}: {len(rows)} prompts {kinds}")


def warm(a):
    os.environ.setdefault("LCB_BUDGET", "3000")
    q = lcb()
    rows = json.load(open(a.corpus))
    t0 = time.monotonic()
    for i, r in enumerate(rows):
        res = q.ask(a.url, "x", r["p"], 7000 + i, "reasoning_budget_tokens", a.max_tokens)
        print(f"[{i + 1}/{len(rows)}] {r['d']}: {res.get('completion_tokens')} tokens, {res.get('decode_tps')} tok/s, "
              f"finish={res.get('finish')}", flush=True)
    print(f"warm: {len(rows)} prompts in {(time.monotonic() - t0) / 60:.0f} min")


def read_profile(path):
    blob = open(path, "rb").read()
    if blob[:4] != b"STRP":
        raise SystemExit(f"{path}: not a Strata profile")
    _, nl, ne, _, n = struct.unpack_from("<5I", blob, 4)
    return [struct.unpack_from("<HH", blob, 24 + 4 * i) for i in range(n)], nl * ne


def compare(a):
    pa, total = read_profile(a.a)
    pb, _ = read_profile(a.b)
    print(f"{a.a}: {len(pa)} ranked pairs; {a.b}: {len(pb)} ranked pairs; {total} in the model")
    for n in (2000, 5000, 10000, 15000, 20000):
        if n <= min(len(pa), len(pb)):
            print(f"  top {n}: {len(set(pa[:n]) & set(pb[:n])) / n:.0%} shared")


def io(a):
    import re
    lines = open(a.log, errors="replace").read().splitlines()
    start = max((i for i, l in enumerate(lines) if "engine started" in l), default=0)
    lines = lines[start:]
    for l in lines:
        if re.search(r"layer split: CUDA\d runs|expert cache \d+ slots|additional experts|RAM budget|resident RAM mode|"
                     r"lend .*slots|memory guard", l):
            print("  " + l[:220])
    os_mb, exp_mb = [], []
    for l in lines:
        m = re.search(r"the OS read ([0-9.]+) MB from storage .* for ([0-9.]+) MB of expert reads", l)
        if m:
            os_mb.append(float(m.group(1))); exp_mb.append(float(m.group(2)))
    last = [l for l in lines if "resident RAM:" in l][-1:]
    print(f"  {len(os_mb)} requests: the OS read {sum(os_mb) / 1000:.1f} GB from the drive for {sum(exp_mb) / 1000:.1f} GB "
          f"of expert reads; per request {', '.join(f'{x / 1000:.1f}' for x in os_mb[-12:])} GB")
    if last:
        print("  " + last[0][:220])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("corpus"); c.add_argument("--out", required=True)
    w = sub.add_parser("warm"); w.add_argument("--url", required=True); w.add_argument("--corpus", required=True)
    w.add_argument("--max-tokens", type=int, default=4500)
    p = sub.add_parser("compare"); p.add_argument("a"); p.add_argument("b")
    i = sub.add_parser("io"); i.add_argument("log")
    a = ap.parse_args()
    {"corpus": corpus, "warm": warm, "compare": compare, "io": io}[a.cmd](a)


if __name__ == "__main__":
    main()
