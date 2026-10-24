#!/usr/bin/env python3
"""longctx: does a model still find facts in a long prompt? Exact-answer recall over a real codebase.

A repository's Python files are concatenated into one prompt per depth (default ~32k, ~128k and ~200k tokens), and
questions whose answers are facts in that code (which file defines a function, the value a constant is set to, a
function's parameters) are asked after it, one per request. The facts come from Python's own parser, so every answer
is checked exactly: no judge. Facts are picked across the whole prompt (start, middle, end). All questions of one
depth share the prompt, so a server with a prefix cache reads it once. Thinking off, greedy: this measures recall, not
reasoning. Why (2026-10-17): the two-3090 bench serves models at 150k-262k context, and long context is where a
coarser quant is expected to lose first.

The lookups turned out too easy to rank models (every arm 11-12/12, 10-18); `build-hard` makes a second set over the
same prompts whose questions need the whole prompt (see "hard set" below). Results: <label>.longctx.jsonl and
<label>.longhard.jsonl; the report flags a depth no arm separates on.

usage: longctx.py build --repo DIR --out SET.json [--depths 32000,128000,200000] [--per-depth 10]
       longctx.py build-hard --from SET.json --out HARD.json [--repo DIR --depths-extra 56000] [--per-kind 5]
       longctx.py run --set SET.json --url http://127.0.0.1:8093/v1 --label NAME --engine vllm|strata [--model NAME] [--only-depth N,M]
       longctx.py report --set SET.json
"""
import argparse, ast, builtins, json, os, random, re, time, urllib.error, urllib.request

CHARS_PER_TOKEN = 3.6   # Qwen tokenizer on Python source, measured on the production 27B; run records the real count
SEED = 20261017
ASK = ("\n\n---\nAnswer this question about the code above. Reply with only the answer on one line, nothing else.\n"
       "Question: {q}")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def py_files(repo):
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__"}
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        out += [os.path.relpath(os.path.join(root, f), repo) for f in sorted(files) if f.endswith(".py")]
    return out


def facts_of(path, src):
    """(kind, name, answer, line) for facts an exact answer can check."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(("where", node.name, path, node.lineno))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            params = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
            if len(params) >= 2:   # *args and **kwargs are part of the answer, not of the choice (keeps picks stable)
                full = [x.arg for x in a.posonlyargs + a.args] + ([f"*{a.vararg.arg}"] if a.vararg else []) \
                    + [x.arg for x in a.kwonlyargs] + ([f"**{a.kwarg.arg}"] if a.kwarg else [])
                out.append(("params", node.name, ", ".join(full), node.lineno))
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, v = node.targets[0].id, node.value
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name) and isinstance(v, ast.Constant) \
                    and isinstance(v.value, (int, float, str)) and not isinstance(v.value, bool) \
                    and len(repr(v.value)) <= 40:
                out.append(("value", name, repr(v.value), node.lineno))
    return out


def question(kind, name, path):
    return {"where": f"In which file is `{name}` defined? Answer with its path as shown in the file header.",
            "params": f"What are the parameters of the function `{name}` in `{path}`, in order? Answer as a "
                      "comma-separated list of their names.",
            "value": f"What value is the constant `{name}` set to in `{path}`? Answer with the value as written "
                     "in the code."}[kind]


def build(a):
    rng = random.Random(SEED)
    files = py_files(a.repo)
    texts = {f: open(os.path.join(a.repo, f), encoding="utf-8", errors="replace").read() for f in files}
    # names defined in more than one file are ambiguous for "where" and "value": left out
    seen = {}
    for f in files:
        for kind, name, _, _ in facts_of(f, texts[f]):
            seen.setdefault((kind, name), set()).add(f)
    out = {"repo": os.path.basename(os.path.abspath(a.repo)), "depths": {}}
    for depth in (int(d) for d in a.depths.split(",")):
        budget, parts, used, starts = depth * CHARS_PER_TOKEN, [], 0, {}
        for f in files:
            block = f"=== {f} ===\n{texts[f]}\n"
            if used + len(block) > budget:
                continue   # skip a file that would overshoot; a smaller one later may fit
            starts[f] = used; parts.append(block); used += len(block)
        context = "".join(parts)
        line_at = {f: [0] + [m.end() for m in re.finditer("\n", texts[f])] for f in starts}   # char offset of each line
        header = {f: len(f"=== {f} ===\n") for f in starts}
        pool = [((starts[f] + header[f] + line_at[f][min(ln - 1, len(line_at[f]) - 1)]) / used, kind, name, ans, f)
                for f in starts for kind, name, ans, ln in facts_of(f, texts[f]) if len(seen[(kind, name)]) == 1]
        pool.sort()
        picked, taken = [], set()   # one fact from each slice of the prompt, kinds in rotation
        for k in range(a.per_depth):
            want = ("where", "value", "params")[k % 3]
            free = [p for p in pool if p not in taken]
            band = [p for p in free if k / a.per_depth <= p[0] < (k + 1) / a.per_depth]
            if not band:   # a slice with no checkable fact (one big file, say): the nearest free facts instead
                mid = (k + 0.5) / a.per_depth
                band = sorted(free, key=lambda p: abs(p[0] - mid))[:8]
            band = [p for p in band if p[1] == want] or band
            if band:
                p = rng.choice(band); taken.add(p)
                pos, kind, name, ans, f = p
                picked.append({"q": question(kind, name, f), "kind": kind, "answer": ans, "pos": round(pos, 3)})
        out["depths"][str(depth)] = {"chars": used, "files": len(parts), "context": context, "questions": picked}
        print(f"depth {depth}: {len(parts)} files, {used} chars (~{int(used / CHARS_PER_TOKEN)} tokens), "
              f"{len(picked)} questions")
    json.dump(out, open(a.out, "w"))


# --- hard set ------------------------------------------------------------------------------------------------------
# Why (2026-10-18): every arm scored 11-12/12 on the lookups above at every depth (27B 4- and 8-bit, Flash-Next
# IQ2_XS and UD-Q4_K_XL), so they say whether the context works, not which model uses it better. Ethan: "If all
# models are passing quality tests, then its a problem with the test itself." These questions need the whole prompt:
# every caller of a function, every function with a given parameter (answers spread over many files), and the one
# call a function makes into another file (find the calls, then find where each is defined). Graded from the parser
# again, with partial credit (F1 over path:function entries) so a near miss still ranks above a blank.
GENERIC = set(dir(builtins)) | {"main", "run", "get", "set", "close", "open", "read", "write", "update", "items",
                                    "append", "format", "join", "split", "strip", "fetch", "render", "start", "stop",
                                    "setup", "execute", "commit", "replace", "load", "dump", "send", "post", "add"}


def defs_of(path, src):
    """Module-level functions and the methods of module-level classes: qualname, kind, parameters, line, and the
    calls in the body (a nested function's calls count for the function around it), as (base name or None, name)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    def calls(fn):
        out = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    out.add((None, f.id))
                elif isinstance(f, ast.Attribute):
                    out.add((f.value.id if isinstance(f.value, ast.Name) else "?", f.attr))
        return out
    def one(n, qual, kind):
        a = n.args
        params = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs] + [x.arg for x in (a.vararg, a.kwarg) if x]
        return {"path": path, "qual": qual, "name": n.name, "kind": kind, "params": params, "line": n.lineno,
                "calls": calls(n)}
    out = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(one(n, n.name, "func"))
        elif isinstance(n, ast.ClassDef):
            out += [one(m, f"{n.name}.{m.name}", "method") for m in n.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return out


def split_context(context):
    """The files of a built context and where each starts: {path: (start offset, text)}."""
    heads = list(re.finditer(r"^=== (.+?) ===\n", context, re.M))
    out = {}
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(context)
        out[h.group(1)] = (h.start(), context[h.end():end].removesuffix("\n"))
    return out


def make_context(files, texts, depth):
    budget, parts, used = depth * CHARS_PER_TOKEN, [], 0
    for f in files:
        block = f"=== {f} ===\n{texts[f]}\n"
        if used + len(block) <= budget:
            parts.append(block); used += len(block)
    return "".join(parts)


def hard_questions(context, per_kind, rng):
    files = split_context(context)
    total = len(context)
    defs = [d for f, (_, t) in files.items() for d in defs_of(f, t)]
    def pos(d):   # where in the prompt a definition sits, 0..1
        start, t = files[d["path"]]
        return (start + len("\n".join(t.split("\n")[:d["line"] - 1]))) / total
    mods = {os.path.splitext(os.path.basename(f))[0] for f in files} | \
           {p for f in files for p in os.path.dirname(f).split("/") if p}
    by_name = {}
    for d in defs:
        by_name.setdefault(d["name"], []).append(d)
    def ent(d):
        return f"{d['path']}:{d['qual']}"
    def resolves(call, name):   # a call of the project function `name`: bare, or through a project module
        base, n = call
        return n == name and (base is None or base in mods)
    cands = {"callers": [], "takes": [], "crosscall": []}
    # callers: a module-level function defined once, called from 2-6 places in 2+ files, never as a method of
    # something that is not a project module (sqlite3.connect is not db.connect)
    for name, ds in by_name.items():
        if len(ds) != 1 or ds[0]["kind"] != "func" or name in GENERIC or len(name) < 5 or name.startswith("test_"):
            continue
        if any(n == name and b not in (None, *mods) for d in defs for b, n in d["calls"]):
            continue
        callers = [d for d in defs if d is not ds[0] and any(resolves(c, name) for c in d["calls"])]
        if 2 <= len(callers) <= 6:
            cands["callers"].append((len({d["path"] for d in callers}) >= 2, pos(ds[0]), f"List every function or method in the code above that calls "
                                     f"`{name}`. Answer as a comma-separated list of `path:function` entries, with "
                                     f"the path as shown in the file header (for a method, `path:Class.method`).",
                                     sorted(ent(d) for d in callers)))
    # takes: a parameter name (not self/cls/args/kwargs) that 3-7 functions in 2+ files take
    params = {}
    for d in defs:
        for p in set(d["params"]) - {"self", "cls", "args", "kwargs"}:
            params.setdefault(p, []).append(d)
    for p, ds in params.items():
        if len(p) >= 3 and 3 <= len(ds) <= 7 and len({d["path"] for d in ds}) >= 2:
            cands["takes"].append((True, min(pos(d) for d in ds), f"List every function or method in the code above that "
                                   f"has a parameter named `{p}`. Answer as a comma-separated list of "
                                   f"`path:function` entries, with the path as shown in the file header (for a "
                                   f"method, `path:Class.method`).", sorted(ent(d) for d in ds)))
    # crosscall: a function whose calls include exactly one project function defined (once) in another file
    quals = {}
    for d in defs:
        quals.setdefault((d["path"], d["qual"]), []).append(d)
    for d in defs:
        if len(quals[(d["path"], d["qual"])]) != 1 or d["name"].startswith("test_"):
            continue
        local = {x["name"] for x in defs if x["path"] == d["path"]}
        # "exactly one" must be true: count every project function elsewhere it calls (names defined twice too),
        # and skip it if it also calls a method that some other file defines (is obj.fetch() a call into http.py?)
        elsewhere = {n for b, n in d["calls"] if n not in local and resolves((b, n), n)
                     and any(x["kind"] == "func" and x["path"] != d["path"] for x in by_name.get(n, []))}
        methods = {n for b, n in d["calls"] if b not in (None, *mods)
                   and any(x["kind"] == "method" and x["path"] != d["path"] for x in by_name.get(n, []))}
        if len(elsewhere) == 1 and not methods and len(by_name[next(iter(elsewhere))]) == 1:
            g = by_name[elsewhere.pop()][0]
            what = "method" if d["kind"] == "method" else "function"
            cands["crosscall"].append((len(d["calls"]) >= 3, pos(d), f"The {what} `{d['qual']}` in `{d['path']}` calls exactly one function "
                                       f"that is defined in a different file of the code above. Which function is "
                                       f"it? Answer as `path:function`, with the path as shown in the file header.",
                                       [ent(g)]))
    # Strict: callers in 2+ files, a function with 3+ calls. A short prompt (32k: 10 files) has too few of those, so
    # it falls back to all candidates (callers in one file, a function with fewer calls).
    picked = []
    for kind, cs in cands.items():   # one question from each slice of the prompt where a slice has one
        if sum(c[0] for c in cs) >= per_kind:
            cs = [c for c in cs if c[0]]
        cs.sort(key=lambda c: c[1])
        n = min(per_kind, len(cs))
        for k in range(n):
            band = cs[k * len(cs) // n:(k + 1) * len(cs) // n]
            _, p, q, ans = rng.choice(band)
            picked.append({"q": q, "kind": kind, "answer": ans, "pos": round(p, 3)})
    return picked, {k: f"{sum(c[0] for c in v)}+{sum(not c[0] for c in v)}" for k, v in cands.items()}


def build_hard(a):
    """The lookup set's contexts (--from, so the prompts and the prefix cache are the same) plus any new depths."""
    rng = random.Random(SEED)
    src = json.load(open(a.from_set))
    depths = {k: v["context"] for k, v in src["depths"].items()}
    if a.depths_extra:
        files = py_files(a.repo)
        texts = {f: open(os.path.join(a.repo, f), encoding="utf-8", errors="replace").read() for f in files}
        for d in a.depths_extra.split(","):
            depths[d] = make_context(files, texts, int(d))
    out = {"repo": src["repo"], "hard": True, "depths": {}}
    for depth in sorted(depths, key=int):
        qs, pool = hard_questions(depths[depth], a.per_kind, rng)
        out["depths"][depth] = {"chars": len(depths[depth]), "files": len(split_context(depths[depth])),
                                "context": depths[depth], "questions": qs}
        print(f"depth {depth}: {len(depths[depth])} chars (~{int(len(depths[depth]) / CHARS_PER_TOKEN)} tokens), "
              f"candidates {pool}, {len(qs)} questions")
    json.dump(out, open(a.out, "w"))


def entries(text):
    out = set()
    for p in re.split(r"[,\n;]+", text):
        p = re.sub(r"[`'\"\s*]", "", re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", p)).removeprefix("./").rstrip(".")
        if ":" in p:
            path, name = p.rsplit(":", 1)
            out.add((path.lower(), name.split(".")[-1].lower()))   # Class.method or method: the same entry
    return out


def grade(qn, text):
    """(exact, score 0..1). Lookups: right or wrong. Lists: F1 of the entries."""
    if qn["kind"] not in ("callers", "takes", "crosscall"):
        ok = correct(qn, text)
        return ok, float(ok)
    got, want = entries(text), entries(", ".join(qn["answer"]))
    if qn["kind"] == "crosscall":
        ok = got == want
        return ok, float(ok)
    hit = len(got & want)
    f1 = 2 * hit / (len(got) + len(want)) if got or want else 0.0
    return got == want, round(f1, 3)


def ask(url, model, engine, prompt, max_tokens=120):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0,
            "stream": True, "stream_options": {"include_usage": True}}
    if engine == "vllm":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    else:   # Strata: its own request field (docs/AI_SETUP.md)
        body["reasoning_effort"] = "none"
    req = urllib.request.Request(url + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic(); first = None; usage = {}; text = []
    with urllib.request.urlopen(req, timeout=1800) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            ev = json.loads(line[5:])
            usage = ev.get("usage") or usage
            for ch in ev.get("choices") or []:
                piece = (ch.get("delta") or {}).get("content") or ""
                if piece:
                    first = first or time.monotonic(); text.append(piece)
    return {"text": "".join(text).strip(), "prompt_tokens": usage.get("prompt_tokens"),
            "ttft_s": round(first - t0, 2) if first else None, "wall_s": round(time.monotonic() - t0, 2)}


def norm(s):
    s = s.strip().splitlines()[0] if s.strip() else ""
    return re.sub(r"[`'\"\s]", "", s).removeprefix("./").rstrip(".").lower()


def correct(qn, text):
    got, want = norm(text), norm(qn["answer"])
    if qn["kind"] == "params":   # "*args" or "args": the names are the answer
        got, want = got.replace("*", ""), want.replace("*", "")
    if qn["kind"] == "value":   # 3 and 3.0, '...' and "...", "\u2014" and '—' are the same answer
        try:
            return float(got) == float(want)
        except ValueError:
            pass
        try:
            return ast.literal_eval(text.strip().splitlines()[0].strip("` ")) == ast.literal_eval(qn["answer"])
        except Exception:
            return got == want
    return got == want


def suffix(s):
    return ".longhard.jsonl" if s.get("hard") else ".longctx.jsonl"


def run(a):
    s = json.load(open(a.set))
    os.makedirs(OUT, exist_ok=True)
    path = f"{OUT}/{a.label}{suffix(s)}"
    only = set(a.only_depth.split(",")) if a.only_depth else None
    with open(path, "a") as f:
        for depth, d in s["depths"].items():
            if only and depth not in only:
                continue
            for i, qn in enumerate(d["questions"]):
                try:
                    res = ask(a.url, a.model, a.engine, d["context"] + ASK.format(q=qn["q"]),
                              400 if s.get("hard") else 120)   # a list of up to 7 entries
                except urllib.error.HTTPError as e:   # a prompt past the server's context comes back as a 400
                    res = {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
                except Exception as e:
                    res = {"error": str(e)[:200]}
                ok, score = grade(qn, res["text"]) if "error" not in res else (False, 0.0)
                row = {"label": a.label, "depth": int(depth), "i": i, "kind": qn["kind"], "pos": qn["pos"],
                       "answer": qn["answer"], "correct": ok, "score": score, **res}
                f.write(json.dumps(row) + "\n"); f.flush()
                print(f"{a.label} {depth:>6} {i:2d} {qn['kind']:9s} {'OK  ' if ok else 'MISS'} {score:.2f} "
                      f"{res.get('prompt_tokens')} tok ttft {res.get('ttft_s')}s  got {res.get('text', res.get('error'))!r:.80}"
                      f"  want {str(qn['answer'])!r:.60}", flush=True)


def report(a):
    s = json.load(open(a.set))   # graded again from the set, so a fixed answer key needs no rerun
    table = {}
    for fn in sorted(os.listdir(OUT)):
        if not fn.endswith(suffix(s)):
            continue
        rows = [json.loads(l) for l in open(f"{OUT}/{fn}")]
        by = {}
        for r in rows:
            if str(r["depth"]) not in s["depths"]:
                continue
            qn = s["depths"][str(r["depth"])]["questions"][r["i"]]
            r["correct"], r["score"] = grade(qn, r["text"]) if "error" not in r else (False, 0.0)
            by.setdefault(r["depth"], []).append(r)
        cells, label = [], fn.removesuffix(suffix(s))
        for depth in sorted(by):
            rs = by[depth]
            toks = [r["prompt_tokens"] for r in rs if r.get("prompt_tokens")]
            fits = "" if not any("error" in r for r in rs) else f" ({sum('error' in r for r in rs)} errors)"
            mean = sum(r["score"] for r in rs) / len(rs)
            table.setdefault(depth, {})[label] = mean
            kinds = ""
            if s.get("hard"):
                kinds = " [" + " ".join(f"{k} {sum(r['score'] for r in rs if r['kind'] == k):.1f}/"
                                        f"{sum(r['kind'] == k for r in rs)}" for k in ("callers", "takes", "crosscall")) + "]"
            cells.append(f"{max(toks) // 1000 if toks else '?'}k {sum(r['correct'] for r in rs)}/{len(rs)} exact, "
                         f"score {mean:.2f}{kinds}{fits}")
        print(f"{label}: " + " | ".join(cells))
    # Does the test separate the arms? One that every arm aces (or every arm fails) ranks nothing (Ethan, 10-18).
    for depth in sorted(table):
        v = table[depth]
        if len(v) < 2:
            continue
        lo, hi = min(v.values()), max(v.values())
        flag = "SATURATED" if lo >= 0.9 else "FLOOR" if hi <= 0.1 else "NO SPREAD" if hi - lo < 0.1 else "spread ok"
        print(f"depth {depth}: {len(v)} arms, scores {lo:.2f}-{hi:.2f}: {flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "build-hard", "run", "report"])
    ap.add_argument("--repo"); ap.add_argument("--out"); ap.add_argument("--set")
    ap.add_argument("--depths", default="32000,128000,200000"); ap.add_argument("--per-depth", type=int, default=10)
    ap.add_argument("--from", dest="from_set"); ap.add_argument("--depths-extra"); ap.add_argument("--per-kind", type=int, default=5)
    ap.add_argument("--url"); ap.add_argument("--label"); ap.add_argument("--engine", choices=["vllm", "strata"])
    ap.add_argument("--model", default="x"); ap.add_argument("--only-depth", help="depths to run, comma-separated")
    a = ap.parse_args()
    {"build": build, "build-hard": build_hard, "run": run, "report": report}[a.cmd](a)


if __name__ == "__main__":
    main()
