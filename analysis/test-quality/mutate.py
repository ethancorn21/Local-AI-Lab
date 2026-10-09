"""mutate.py gen <repo> <n> <seed> <out.json> : pick n single-point mutants in the app code (Python, page JS, templates).
Each mutant: file, line, original line, mutated line, operator. Python mutants are checked to compile, JS with node."""
import ast, json, os, random, re, subprocess, sys

def py_sites(path, src):
    lines = src.splitlines()
    sites = []
    tree = ast.parse(src)
    for n in ast.walk(tree):
        ln = getattr(n, "lineno", None)
        if ln is None or getattr(n, "end_lineno", ln) != ln:
            continue
        line = lines[ln - 1]
        if line.strip().startswith(("#", "def ", "class ", "@", "import ", "from ")):
            continue
        if isinstance(n, ast.Compare) and len(n.ops) == 1:
            swap = {ast.Lt: ("<", "<="), ast.LtE: ("<=", "<"), ast.Gt: (">", ">="), ast.GtE: (">=", ">"),
                    ast.Eq: ("==", "!="), ast.NotEq: ("!=", "=="), ast.In: (" in ", " not in "), ast.NotIn: (" not in ", " in ")}
            t = swap.get(type(n.ops[0]))
            if t and line.count(t[0]) == 1:
                sites.append(("compare", ln, line.replace(t[0], t[1])))
        elif isinstance(n, ast.BoolOp) and line.count(" and ") + line.count(" or ") == 1:
            sites.append(("and/or", ln, line.replace(" and ", " or ") if " and " in line else line.replace(" or ", " and ")))
        elif isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            s = ast.get_source_segment(src, n)
            if s and line.count(s) == 1 and re.search(r"(?<![\w.])" + re.escape(s) + r"(?![\w.])", line):
                new = str(n.value + 1) if isinstance(n.value, int) else repr(round(n.value * 1.5, 6))
                sites.append(("constant", ln, re.sub(r"(?<![\w.])" + re.escape(s) + r"(?![\w.])", new, line, count=1)))
        elif isinstance(n, (ast.Expr, ast.Assign, ast.AugAssign)) and not isinstance(getattr(n, "value", None), ast.Constant):
            ind = line[: len(line) - len(line.lstrip())]
            sites.append(("delete statement", ln, ind + "pass"))
        elif isinstance(n, ast.If):
            ind = line[: len(line) - len(line.lstrip())]
            m = re.match(r"(\s*(?:el)?if )(.*):\s*$", line)
            if m and n.lineno == ln:
                sites.append(("negate condition", ln, f"{m.group(1)}not ({m.group(2)}):"))
    return sites

def js_sites(src):
    sites = []
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith(("//", "*", "/*")):
            continue
        for a, b, op in (("===", "!==", "compare"), ("!==", "===", "compare"), (" < ", " <= ", "compare"), (" > ", " >= ", "compare"),
                         ("&&", "||", "and/or"), ("true", "false", "constant")):
            if line.count(a) == 1:
                sites.append((op, i, line.replace(a, b)))
                break
        if s.endswith(";") and not s.startswith(("return", "const", "let", "var", "}")) and "(" in s:
            ind = line[: len(line) - len(line.lstrip())]
            sites.append(("delete statement", i, ind + "/* removed */"))
    return sites

def tpl_sites(src):
    sites = []
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(r"\{\{[^}]+\}\}", line) and "{%" not in line:
            sites.append(("drop template value", i, re.sub(r"\{\{[^}]+\}\}", "", line, count=1)))
    return sites

def gen(repo, n, seed, out):
    rnd = random.Random(seed)
    pool = []
    for dp, _, fs in os.walk(os.path.join(repo, "frontpage")):
        for f in fs:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, repo)
            src = open(p, errors="replace").read()
            if f.endswith(".py"):
                try:
                    s = py_sites(p, src)
                except SyntaxError:
                    continue
            elif f.endswith(".js"):
                s = js_sites(src)
            elif f.endswith(".html"):
                s = tpl_sites(src)
            else:
                continue
            pool += [(rel, *x) for x in s]
    # stratify: Python 60%, JS 25%, templates 15%
    by = {"py": [x for x in pool if x[0].endswith(".py")], "js": [x for x in pool if x[0].endswith(".js")],
          "html": [x for x in pool if x[0].endswith(".html")]}
    want = {"py": round(n * .6), "js": round(n * .25)}
    want["html"] = n - want["py"] - want["js"]
    picked = []
    for k, m in want.items():
        cand = by[k][:]
        rnd.shuffle(cand)
        for rel, op, ln, new in cand:
            if len([x for x in picked if x["kind"] == k]) >= m:
                break
            lines = open(os.path.join(repo, rel)).read().splitlines(keepends=True)
            orig = lines[ln - 1]
            nl = new + ("\n" if orig.endswith("\n") else "")
            if nl == orig:
                continue
            lines[ln - 1] = nl
            txt = "".join(lines)
            if k == "py":
                try:
                    compile(txt, rel, "exec")
                except SyntaxError:
                    continue
            if k == "js":
                tmp = "/tmp/_mut_check.js"
                open(tmp, "w").write(txt)
                if subprocess.run(["node", "--check", tmp], capture_output=True).returncode:
                    continue
            picked.append(dict(id=len(picked), kind=k, file=rel, line=ln, op=op, orig=orig.rstrip("\n"), new=new))
    json.dump(picked, open(out, "w"), indent=1)
    print(len(pool), "sites;", {k: len(v) for k, v in by.items()}, "picked", len(picked))

if __name__ == "__main__":
    if sys.argv[1] == "gen":
        gen(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
