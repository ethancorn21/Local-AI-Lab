#!/usr/bin/env python3
"""lcb-quick: a quick single-shot coding capability check on LiveCodeBench (one attempt per problem, hidden tests).

Same request settings as production (thinking on at effort xhigh, temperature 1.0 / top_p 0.95 / top_k 20, 16k
thinking cap) and the same problems for every model: AtCoder (stdin/stdout) problems from LiveCodeBench release v6
(2025-01..04), N_HARD hard + N_MED medium, picked with a fixed seed. The prompt is LiveCodeBench's own for stdin
problems. Solutions run as the `agent` account (the account that runs model-written code every day), each test with a
time limit; output compared token by token (floats with a 1e-6 tolerance).

usage: [LCB_DATA=heldout.jsonl LCB_TIMEOUT=20] lcb-quick.py gen --url http://127.0.0.1:8082/v1 --label strata-iq2xs [--model NAME] [--budget-field thinking_token_budget | --wrap-up]
       lcb-quick.py grade --label strata-iq2xs
       lcb-quick.py report
"""
import argparse, base64, json, os, pickle, random, re, subprocess, sys, tempfile, time, urllib.request, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
# LCB_DATA: a file in LiveCodeBench's row format; default LiveCodeBench v6 (huggingface.co/datasets/livecodebench/
# code_generation_lite test6.jsonl). A private held-out set (platform "heldout") is used whole, not sampled.
DATA = os.environ.get("LCB_DATA", os.path.join(HERE, "test6.jsonl"))
OUT = os.path.join(HERE, "results")
N_HARD, N_MED, SEED = 12, 8, 20261005
TEST_TIMEOUT = int(os.environ.get("LCB_TIMEOUT", "10"))   # seconds per test
PROMPT = ("You will be given a question (problem specification) and will generate a correct Python program that matches "
          "the specification and passes all tests.\n\nQuestion: {q}\n\nRead the inputs from stdin solve the problem and write "
          "the answer to stdout (do not directly test on the sample inputs). Enclose your code within delimiters as follows. "
          "Ensure that when the python program runs, it reads the inputs, runs the algorithm and writes output to STDOUT.\n"
          "```python\n# YOUR CODE HERE\n```\n\n### Answer: (use the provided format with backticks)\n")


def problems():
    rows = [json.loads(l) for l in open(DATA)]
    if all(r["platform"] == "heldout" for r in rows):
        return sorted(rows, key=lambda r: r["question_id"])
    rows = [r for r in rows if r["platform"] == "atcoder"]
    rng = random.Random(SEED)
    pick = []
    for diff, n in (("hard", N_HARD), ("medium", N_MED)):
        pool = sorted((r for r in rows if r["difficulty"] == diff), key=lambda r: r["question_id"])
        pick += rng.sample(pool, n)
    return pick


def tests(r):
    priv = r["private_test_cases"]
    try:
        priv = json.loads(priv)
    except ValueError:
        priv = json.loads(pickle.loads(zlib.decompress(base64.b64decode(priv.encode()))))
    return json.loads(r["public_test_cases"]) + priv


BUDGET = int(os.environ.get("LCB_BUDGET", "16384"))   # thinking cap, as in production
# Strata's wrap-up at the thinking cap (serve/server.py REASONING_WRAP_UP). vLLM's thinking_token_budget forces a bare
# </think> instead, after which the 27B kept reasoning in the answer until max_tokens: --wrap-up gives vLLM models
# Strata's ending, from the client.
WRAP_UP = "\n\nI have thought about this long enough; time to give my answer.\n</think>\n\n"


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.load(resp)


def events(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                yield json.loads(line[5:])


def ask(url, model, prompt, seed, budget_field, max_tokens=24000, wrap_up=False):
    msgs = [{"role": "user", "content": prompt}]
    kwargs = {"enable_thinking": True, "preserve_thinking": True, "reasoning_effort": "xhigh"}
    sampling = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "seed": seed}
    body = {"model": model, "messages": msgs, "max_tokens": max_tokens, **sampling, "stream": True,
            "stream_options": {"include_usage": True, "continuous_usage_stats": wrap_up}, "chat_template_kwargs": kwargs}
    if budget_field and not wrap_up:   # vLLM: the per-response thinking cap is a request field (Strata: server config)
        body[budget_field] = BUDGET
    t0 = time.monotonic(); first = last = None; usage = {}; content = []; reasoning = []; finish = None; cut = False
    for ev in events(url + "/chat/completions", body):
        usage = ev.get("usage") or usage
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or {}
            finish = ch.get("finish_reason") or finish
            content.append(d.get("content") or "")
            reasoning.append(d.get("reasoning") or d.get("reasoning_content") or "")
            if d.get("content") or d.get("reasoning_content") or d.get("reasoning"):
                now = time.monotonic(); first = first or now; last = now
        if wrap_up and not "".join(content).strip() and (usage.get("completion_tokens") or 0) >= BUDGET:
            cut = True   # leaving the stream aborts the request
            break
    n = usage.get("completion_tokens") or 0
    if cut:   # continue from prompt + thinking + wrap-up as raw tokens (the prefix cache holds most of it)
        base = url.rsplit("/v1", 1)[0]
        ids = post(url + "/chat/completions/render", {"model": model, "messages": msgs, "chat_template_kwargs": kwargs})["token_ids"]
        tail = post(base + "/tokenize", {"model": model, "prompt": "".join(reasoning) + WRAP_UP, "add_special_tokens": False})["tokens"]
        body2 = {"model": model, "prompt": ids + tail, "max_tokens": max(1, max_tokens - len(tail)), **sampling,
                 "stream": True, "stream_options": {"include_usage": True}}
        content, usage2 = [], {}
        for ev in events(url + "/completions", body2):
            usage2 = ev.get("usage") or usage2
            for ch in ev.get("choices") or []:
                finish = ch.get("finish_reason") or finish
                if ch.get("text"):
                    content.append(ch["text"]); last = time.monotonic()
        n = len(tail) + (usage2.get("completion_tokens") or 0)
    return {"text": "".join(content), "completion_tokens": n, "finish": finish, "wall_s": round(time.monotonic() - t0, 1),
            "decode_tps": round((n - 1) / (last - first), 1) if first and last and last > first and n > 1 else None,
            **({"wrapped_up": cut} if wrap_up else {})}


def code_of(text):
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    return blocks[-1] if blocks else None


def same(got, want):
    g, w = got.split(), want.split()
    if g == w:
        return True
    if len(g) != len(w):
        return False
    for a, b in zip(g, w):
        if a == b:
            continue
        try:
            if abs(float(a) - float(b)) > 1e-6 * max(1.0, abs(float(b))):
                return False
        except ValueError:
            return False
    return True


def run_tests(code, cases):
    with tempfile.TemporaryDirectory() as d:
        os.chmod(d, 0o755)
        src = os.path.join(d, "sol.py")
        open(src, "w").write(code)
        os.chmod(src, 0o644)
        for i, c in enumerate(cases):
            try:
                p = subprocess.run(["sudo", "-u", "agent", "timeout", str(TEST_TIMEOUT), "python3", src], input=c["input"],
                                   capture_output=True, text=True, timeout=TEST_TIMEOUT + 5, cwd="/tmp")
            except subprocess.TimeoutExpired:
                return {"passed": False, "failed_at": i, "why": "timeout"}
            if p.returncode == 124:
                return {"passed": False, "failed_at": i, "why": "timeout"}
            if p.returncode:
                return {"passed": False, "failed_at": i, "why": "error: " + p.stderr.strip()[-200:]}
            if not same(p.stdout, c["output"]):
                return {"passed": False, "failed_at": i, "why": "wrong answer"}
    return {"passed": True, "tests": len(cases)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gen", "grade", "report"])
    ap.add_argument("--url"); ap.add_argument("--label"); ap.add_argument("--budget-field")
    ap.add_argument("--model", default="x")   # vLLM checks the name; Strata serves any
    ap.add_argument("--max-tokens", type=int, default=24000)
    ap.add_argument("--redo-length", action="store_true", help="re-ask only the answers that hit max_tokens (old file kept)")
    ap.add_argument("--wrap-up", action="store_true", help="vLLM: end capped thinking with Strata's wrap-up, not a bare </think>")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.cmd == "gen" and a.redo_length:
        path = f"{OUT}/{a.label}.gen.jsonl"
        old = [json.loads(l) for l in open(path)]
        os.replace(path, f"{path}.max{old[0].get('max_tokens', 24000)}")
        seeds = {r["question_id"]: SEED + k for k, r in enumerate(problems())}
        by_id = {r["question_id"]: r for r in problems()}
        with open(path, "w") as f:
            for g in old:
                if g["finish"] == "length":
                    res = ask(a.url, a.model, PROMPT.format(q=by_id[g["qid"]]["question_content"]), seeds[g["qid"]], a.budget_field, a.max_tokens, a.wrap_up)
                    g = {**g, **res, "max_tokens": a.max_tokens}
                    print(f"{a.label} redo {g['qid']:10s} tokens {res['completion_tokens']:6d} {res['wall_s']:6.1f}s finish={res['finish']}", flush=True)
                f.write(json.dumps(g) + "\n"); f.flush()
    elif a.cmd == "gen":
        path = f"{OUT}/{a.label}.gen.jsonl"
        done = {json.loads(l)["qid"] for l in open(path)} if os.path.exists(path) else set()
        for k, r in enumerate(problems()):
            if r["question_id"] in done:
                continue
            res = ask(a.url, a.model, PROMPT.format(q=r["question_content"]), SEED + k, a.budget_field, a.max_tokens, a.wrap_up)
            rec = {"qid": r["question_id"], "difficulty": r["difficulty"], "title": r["question_title"], **res}
            open(path, "a").write(json.dumps(rec) + "\n")
            print(f"{a.label} {k + 1:2d} {r['difficulty']:6s} {r['question_id']:10s} tokens {res['completion_tokens']:6d} "
                  f"{res['wall_s']:6.1f}s {res['decode_tps']} tok/s finish={res['finish']}", flush=True)
    elif a.cmd == "grade":
        by_id = {r["question_id"]: r for r in problems()}
        out = []
        for l in open(f"{OUT}/{a.label}.gen.jsonl"):
            g = json.loads(l)
            code = code_of(g["text"])
            res = run_tests(code, tests(by_id[g["qid"]])) if code else {"passed": False, "why": "no code block"}
            out.append({"qid": g["qid"], "difficulty": g["difficulty"], **res})
            print(a.label, g["qid"], g["difficulty"], json.dumps(res)[:150], flush=True)
        json.dump(out, open(f"{OUT}/{a.label}.grade.json", "w"), indent=1)
    else:
        for f in sorted(os.listdir(OUT)):
            if not f.endswith(".grade.json"):
                continue
            label = f[:-11]
            gr = json.load(open(f"{OUT}/{f}"))
            gen = [json.loads(l) for l in open(f"{OUT}/{label}.gen.jsonl")]
            kinds = sorted({x["difficulty"] for x in gr})
            by = ", ".join(f"{k} {sum(x['passed'] for x in gr if x['difficulty'] == k)}/{sum(1 for x in gr if x['difficulty'] == k)}" for k in kinds)
            toks = sorted(g["completion_tokens"] for g in gen)
            capped = sum(1 for g in gen if g["finish"] == "length")
            print(f"{label}: {sum(x['passed'] for x in gr)}/{len(gr)} passed ({by}); output tokens median {toks[len(toks) // 2]}, "
                  f"total {sum(toks)}; wall {sum(g['wall_s'] for g in gen) / 60:.1f} min; hit max_tokens {capped}")


if __name__ == "__main__":
    main()
