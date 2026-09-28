#!/usr/bin/env python3
"""engine-bench: one client, identical measurement for any OpenAI-compatible server (llama.cpp, vLLM, NInfer).

Production-like request: thinking on at reasoning effort xhigh, temperature 1.0 / top_p 0.95 / top_k 20, 1500 new
tokens, a short coding prompt and a ~45k-token coding prompt (hollowdeep code as context), N reps with seeds 1..N.
Streams the response; decode tok/s = (completion_tokens - 1) / (last token time - first token time), TTFT = first
token time - send time. completion_tokens comes from the server's usage block (includes reasoning tokens).

usage: engine-bench.py --url http://127.0.0.1:8099/v1 --model NAME --label LABEL --effort kwargs|toplevel [--reps 3]
"""
import argparse, json, time, urllib.request

SHORT = ("Write a JavaScript ES module implementing a binary min-heap priority queue with push, pop, peek, size and "
         "decreaseKey(id, newPriority) (O(log n) via an id->index map), with JSDoc, then node:test unit tests "
         "covering every method.")
LONG_FILE = "/opt/llm/sweep/long-prompt.txt"


def run(url, model, prompt, seed, effort):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1500,
            "temperature": 1.0, "top_p": 0.95, "top_k": 20, "seed": seed, "stream": True,
            "stream_options": {"include_usage": True}}
    if effort == "kwargs":   # llama.cpp / vLLM: the chat template reads these
        body["chat_template_kwargs"] = {"enable_thinking": True, "preserve_thinking": True, "reasoning_effort": "xhigh"}
    else:                    # NInfer: top-level field
        body["reasoning_effort"] = "xhigh"
    req = urllib.request.Request(url + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic(); first = last = None; usage = {}; chunks = 0
    with urllib.request.urlopen(req, timeout=1800) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            ev = json.loads(data)
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content") or d.get("reasoning_content") or d.get("reasoning"):
                    now = time.monotonic(); first = first or now; last = now; chunks += 1
    n = usage.get("completion_tokens") or 0
    return {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": n, "chunks": chunks,
            "ttft_s": round(first - t0, 2) if first else None,
            "decode_tps": round((n - 1) / (last - first), 1) if first and last and last > first and n > 1 else None}


def main():
    ap = argparse.ArgumentParser()
    for a in ("--url", "--model", "--label"):
        ap.add_argument(a, required=True)
    ap.add_argument("--effort", choices=["kwargs", "toplevel"], required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default="/opt/llm/sweep/results/engines.jsonl")
    a = ap.parse_args()
    prompts = {"short": SHORT, "long": open(LONG_FILE).read()}
    for kind, p in prompts.items():
        for seed in range(1, a.reps + 1):
            try:
                res = run(a.url, a.model, p, seed, a.effort)
            except Exception as exc:  # record, keep going
                res = {"error": str(exc)[:200]}
            row = {"label": a.label, "prompt": kind, "seed": seed, **res}
            print(json.dumps(row), flush=True)
            open(a.out, "a").write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
