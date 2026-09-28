#!/usr/bin/env python3
"""engine-ppl: perplexity on IDENTICAL token ids across engines, matching llama-perplexity's definition
(-c 4096 --chunks 12: each 4096-token window is evaluated from scratch, only its second half is scored).

Token ids come from llama.cpp's tokenizer (tokens.json, written by `llama-tokenize --ids`), so every engine scores
exactly the same sequence. vLLM: /v1/completions with prompt = token ids, max_tokens 1, prompt_logprobs 0.

usage: engine-ppl.py --url http://127.0.0.1:18020/v1 --model NAME --label LABEL [--tokens tokens.json]
"""
import argparse, json, math, urllib.request

CTX, CHUNKS = 4096, 12


def logprobs_vllm(url, model, ids):
    body = {"model": model, "prompt": ids, "max_tokens": 1, "temperature": 0, "prompt_logprobs": 0}
    req = urllib.request.Request(url + "/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        pl = json.load(r)["choices"][0]["prompt_logprobs"]
    out = []
    for pos, entry in enumerate(pl):          # entry: {token_id: {"logprob": x, ...}, ...} or None at position 0
        if entry is None:
            out.append(None); continue
        want = str(ids[pos])
        out.append(entry[want]["logprob"] if want in entry else next(iter(entry.values()))["logprob"])
    return out


def main():
    ap = argparse.ArgumentParser()
    for a in ("--url", "--model", "--label"):
        ap.add_argument(a, required=True)
    ap.add_argument("--tokens", default="/opt/llm/sweep/tokens.json")
    ap.add_argument("--out", default="/opt/llm/sweep/results/engine-ppl.jsonl")
    ap.add_argument("--protocol", choices=["llama", "ninfer"], default="llama",
                    help="llama: 12 x 4096 windows, second half scored (llama-perplexity). ninfer: one stream, "
                         "context 4096 / stride 2048, every token after the first scored once (ninfer-perplexity)")
    a = ap.parse_args()
    ids = json.load(open(a.tokens))
    windows = []  # (start, end, first scored position)
    if a.protocol == "llama":
        assert len(ids) >= CTX * CHUNKS, f"need {CTX * CHUNKS} tokens, have {len(ids)}"
        windows = [(c * CTX, (c + 1) * CTX, c * CTX + CTX // 2) for c in range(CHUNKS)]
    else:
        stride = CTX // 2
        windows.append((0, min(CTX, len(ids)), 1))
        s = CTX
        while s < len(ids):
            windows.append((s - (CTX - stride), min(s + stride, len(ids)), s))
            s += stride
    nll, n = 0.0, 0
    for i, (b, e, first) in enumerate(windows):
        lp = logprobs_vllm(a.url, a.model, ids[b:e])
        scored = [x for x in lp[first - b:] if x is not None]
        nll -= sum(scored); n += len(scored)
        print(f"window {i + 1}/{len(windows)} running PPL {math.exp(nll / n):.4f}", flush=True)
    row = {"label": a.label, "protocol": a.protocol, "ppl": round(math.exp(nll / n), 6), "scored_tokens": n}
    print(json.dumps(row)); open(a.out, "a").write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
