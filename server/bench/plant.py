#!/usr/bin/env python3
"""plant: copy Python code and plant known one-token bugs in it, for scouts to find (scout.py grade).

Each bug is one token changed inside a function body, of a kind that slips through review: a comparison's boundary
(< and <=), == and !=, and and or, a dropped `not`, min and max, + 1 and - 1, an index [0]/[1]/[-1], True and False
returned. Tokens come from Python's tokenizer, so nothing inside a string or comment is touched; the file must still
parse and its AST must change. At most one bug per function, spread over the files by size. The manifest (path,
line, the line before and after, the kind) is the answer key: keep it off the machine the scouts run on.
Why (2026-10-17): a scout swarm's quality needs a test with known answers on real code; planted bugs on the owner's
own repositories are private and objective.

usage: plant.py --src DIR[:SUBDIR,...] ... --out TARGET_DIR --manifest MANIFEST.json --count 60 --seed N
(the seed is kept with the manifest, off this public repository: with the same sources it rebuilds the answer key)
"""
import argparse, ast, io, json, os, random, shutil, tokenize

SWAP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==", "and": "or", "or": "and",
        "min": "max", "max": "min", "True": "False", "False": "True"}
KIND = {"<": "boundary", "<=": "boundary", ">": "boundary", ">=": "boundary", "==": "equality", "!=": "equality",
        "and": "and/or", "or": "and/or", "min": "min/max", "max": "min/max", "True": "return value",
        "False": "return value", "not": "dropped not", "1": "off by one", "0": "index", "-1": "index"}


def candidates(src):
    """(line, col, old, new, kind, function) for every token a bug can be planted at."""
    tree = ast.parse(src)
    owner = {}   # line -> innermost function body it is in
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            start = first.end_lineno + 1 if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                and isinstance(first.value.value, str) else first.lineno   # skip the docstring
            for ln in range(start, node.end_lineno + 1):
                if ln not in owner or owner[ln][1] > node.end_lineno - node.lineno:
                    owner[ln] = (f"{node.name}@{node.lineno}", node.end_lineno - node.lineno)
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    out = []
    for i, t in enumerate(toks):
        ln, col = t.start
        if ln not in owner:
            continue
        fn = owner[ln][0]
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t.type == tokenize.OP and t.string in ("<", "<=", ">", ">=", "==", "!="):
            out.append((ln, col, t.string, SWAP[t.string], KIND[t.string], fn))
        elif t.type == tokenize.NAME and t.string in ("and", "or"):
            out.append((ln, col, t.string, SWAP[t.string], KIND[t.string], fn))
        elif t.type == tokenize.NAME and t.string in ("min", "max") and nxt is not None and nxt.string == "(":
            out.append((ln, col, t.string, SWAP[t.string], KIND[t.string], fn))
        elif t.type == tokenize.NAME and t.string in ("True", "False") and prev is not None and prev.string == "return":
            out.append((ln, col, t.string, SWAP[t.string], KIND[t.string], fn))
        elif t.type == tokenize.NAME and t.string == "not" and prev is not None and prev.string in ("if", "elif", "while") \
                and nxt is not None and nxt.type == tokenize.NAME:
            out.append((ln, col, "not ", "", KIND["not"], fn))
        elif t.type == tokenize.NUMBER and t.string == "1" and prev is not None and prev.string in ("+", "-") \
                and toks[i - 2].type in (tokenize.NAME, tokenize.OP) and toks[i - 2].string not in ("(", "[", ",", "="):
            out.append((ln, prev.start[1], f"{prev.string} 1" if src.splitlines()[ln - 1][prev.start[1]:t.end[1]] == f"{prev.string} 1"
                        else None, "- 1" if prev.string == "+" else "+ 1", KIND["1"], fn))
        elif t.type == tokenize.NUMBER and t.string in ("0", "1") and prev is not None and prev.string == "[" \
                and nxt is not None and nxt.string == "]":
            out.append((ln, col, t.string, "1" if t.string == "0" else "0", KIND["0"], fn))
    return [c for c in out if c[2] is not None]


def apply(src, ln, col, old, new):
    lines = src.splitlines(keepends=True)
    line = lines[ln - 1]
    if line[col:col + len(old)] != old:
        return None
    lines[ln - 1] = line[:col] + new + line[col + len(old):]
    return "".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", action="append", required=True, help="DIR or DIR:sub1,sub2 (copied as <basename>/...)")
    ap.add_argument("--out", required=True); ap.add_argument("--manifest", required=True)
    ap.add_argument("--count", type=int, default=60); ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    files = []
    for spec in a.src:
        root, _, subs = spec.partition(":")
        name = os.path.basename(os.path.abspath(root))
        for sub in (subs.split(",") if subs else ["."]):
            for r, ds, fs in os.walk(os.path.join(root, sub)):
                ds[:] = sorted(d for d in ds if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".venv"))
                for f in sorted(fs):
                    if f.endswith(".py"):
                        rel = os.path.join(name, os.path.relpath(os.path.join(r, f), root))
                        dst = os.path.join(a.out, rel)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(os.path.join(r, f), dst)
                        files.append(rel)
    pool = []
    for rel in files:
        src = open(os.path.join(a.out, rel), encoding="utf-8").read()
        try:
            pool += [(rel, *c) for c in candidates(src)]
        except (SyntaxError, tokenize.TokenError):
            continue
    rng.shuffle(pool)
    bugs, used_fn, edits = [], set(), {}
    for rel, ln, col, old, new, kind, fn in pool:
        if len(bugs) >= a.count or (rel, fn) in used_fn or any(b["path"] == rel and abs(b["line"] - ln) < 8 for b in bugs):
            continue
        path = os.path.join(a.out, rel)
        src = open(path, encoding="utf-8").read()
        new_src = apply(src, ln, col, old, new)
        if new_src is None:
            continue
        try:
            if ast.dump(ast.parse(new_src)) == ast.dump(ast.parse(src)):
                continue
        except SyntaxError:
            continue
        open(path, "w", encoding="utf-8").write(new_src)
        used_fn.add((rel, fn))
        bugs.append({"path": rel, "line": ln, "op": kind, "function": fn.split("@")[0],
                     "before": src.splitlines()[ln - 1].strip(), "after": new_src.splitlines()[ln - 1].strip()})
    lines = sum(sum(1 for _ in open(os.path.join(a.out, f), encoding="utf-8", errors="replace")) for f in files)
    json.dump({"files": len(files), "lines": lines, "seed": a.seed, "bugs": sorted(bugs, key=lambda b: (b["path"], b["line"]))},
              open(a.manifest, "w"), indent=1)
    kinds = {}
    for b in bugs:
        kinds[b["op"]] = kinds.get(b["op"], 0) + 1
    print(f"{len(files)} files, {lines} lines, {len(bugs)} bugs planted: {kinds}")


if __name__ == "__main__":
    main()
