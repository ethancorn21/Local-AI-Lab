"""classify.py <repo> : classify every pytest test function of a frontpage checkout by what it exercises (static, AST).
browser  : drives a real browser (playwright, or runs an e2e/*.spec.mjs)
http     : talks to the app over HTTP or a booted service (test client, live server, subprocess CLI)
golden   : compares output with an approved file in the repo
direct   : calls the app's Python functions directly, no HTTP, no browser (unit-like)
other    : none of the above (pure checks of files, configs)"""
import ast, os, re, json, collections

import sys
ROOT = sys.argv[1]
BROWSER = re.compile(r"playwright|chromium|\.spec\.mjs|npx|page\.goto|run_spec|node_spec")
HTTP = re.compile(r"test_client|client\.(get|post|put|delete)|\bclient\b|urlopen|requests\.|http://|live_server|serve|boot|subprocess|Popen|base_url|port")
GOLDEN = re.compile(r"golden|approved")
out = collections.Counter()
pertest = {}
per_file = {}
examples = collections.defaultdict(list)
for f in sorted(os.listdir(f"{ROOT}/tests")):
    if not (f.startswith("test_") and f.endswith(".py")):
        continue
    src = open(f"{ROOT}/tests/{f}").read()
    tree = ast.parse(src)
    # module-level helpers: name -> source, so a test that calls a helper inherits the helper's kind
    helpers = {n.name: ast.get_source_segment(src, n) or "" for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("test_")}
    fixtures = helpers  # fixtures are module functions too
    c = collections.Counter()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_"):
            body = ast.get_source_segment(src, n) or ""
            args = [a.arg for a in n.args.args]
            ctx = body
            for name in set(re.findall(r"\b([a-zA-Z_]\w*)\b", body)) | set(args):
                if name in helpers:
                    ctx += "\n" + helpers[name]
            if BROWSER.search(ctx):
                k = "browser"
            elif GOLDEN.search(ctx):
                k = "golden"
            elif HTTP.search(ctx) or any(a in ("client", "app", "server", "live", "service") for a in args):
                k = "http"
            elif re.search(r"\bfrom frontpage|import frontpage|frontpage\.", src) and re.search(r"\w+\(", body):
                k = "direct"
            else:
                k = "other"
            c[k] += 1
            pertest[f"tests/{f}::{n.name}"] = k
            out[k] += 1
            if len(examples[k]) < 6 and k == "direct":
                examples[k].append(f"{f}::{n.name}")
    per_file[f] = dict(c)
print(json.dumps({"total": dict(out), "per_file": per_file, "pertest": pertest}, indent=0))
