"""Mutation score of an arm's own test suite: plant one small bug at a time in its product code and count how many
its tests catch. A suite that passes against a planted bug did not test that behaviour.

Usage (as agent, on a COPY of the project):  mutate.py PROJECT_COPY N SEED   (prints one JSON object)
Operators: comparison flips (< <=, > >=, == !=, in / not in, is / is not), + <-> -, and <-> or, `not x` -> `not not x`,
True <-> False, integer constant n -> n + 1. Product code = tracked .py files outside tests/ (and not conftest.py).
A mutant is killed when the suite fails, errors or times out; mutants that do not compile are skipped.
"""
import ast
import json
import os
import random
import subprocess
import sys
import time

SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
        ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}


def sites(tree):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in SWAP:
                    out.append(("cmp", node, i))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            out.append(("arith", node, None))
        elif isinstance(node, ast.BoolOp):
            out.append(("bool", node, None))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            out.append(("not", node, None))
        elif isinstance(node, ast.Constant) and type(node.value) is bool:
            out.append(("const_bool", node, None))
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            out.append(("const_int", node, None))
    return out


def apply(kind, node, i):
    if kind == "cmp":
        node.ops[i] = SWAP[type(node.ops[i])]()
    elif kind == "arith":
        node.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
    elif kind == "bool":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif kind == "not":
        node.operand = ast.UnaryOp(op=ast.Not(), operand=node.operand)   # not x -> not (not x): the condition flips, any type
    elif kind == "const_bool":
        node.value = not node.value
    elif kind == "const_int":
        node.value += 1


def run_tests(root, timeout):
    t = time.time()
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=root,
                           capture_output=True, timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return p.returncode, time.time() - t
    except subprocess.TimeoutExpired:
        return "timeout", timeout


def main():
    root, n, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    files = subprocess.run(["git", "-C", root, "ls-files", "*.py"], capture_output=True, text=True).stdout.split()
    files = [f for f in files if not f.startswith("tests/") and "/tests/" not in f and not f.endswith("conftest.py")
             and not os.path.basename(f).startswith("test_")]
    rc, base_t = run_tests(root, 600)
    if rc not in (0,):
        print(json.dumps({"baseline": f"suite not green ({rc})", "score": None}))
        return
    timeout = max(30, min(300, 3 * base_t + 10))
    cands = []
    for f in files:
        try:
            src = open(os.path.join(root, f)).read()
            tree = ast.parse(src)
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        for k in range(len(sites(tree))):
            cands.append((f, k))
    rng = random.Random(seed)
    pick = rng.sample(cands, min(n, len(cands)))
    killed = survived = skipped = 0
    survivors = []
    for f, k in pick:
        path = os.path.join(root, f)
        src = open(path).read()
        tree = ast.parse(src)
        kind, node, i = sites(tree)[k]
        apply(kind, node, i)
        try:
            mutated = ast.unparse(tree)
            compile(mutated, path, "exec")
        except Exception:
            skipped += 1
            continue
        try:
            open(path, "w").write(mutated)
            rc, _ = run_tests(root, timeout)
        finally:
            open(path, "w").write(src)
        if rc == 0:
            survived += 1
            survivors.append(f"{f}: {kind} at line {getattr(node, 'lineno', '?')}")
        else:
            killed += 1
    total = killed + survived
    print(json.dumps({"baseline_s": round(base_t, 1), "sites": len(cands), "mutants": total, "killed": killed,
                      "survived": survived, "skipped": skipped, "score": round(killed / total, 3) if total else None,
                      "survivors": survivors[:15]}))


if __name__ == "__main__":
    main()
