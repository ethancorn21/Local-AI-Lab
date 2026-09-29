"""Zero-shot decoder as a one-pass classifier: read the next-token probabilities of the answer, never generate.

p(malicious) = p(" yes") / (p(" yes") + p(" no")) after the question; the tactic is read the same way over option
letters. Thinking is switched off (the answer is the first assistant token). Local HF models, or an OpenAI-compatible
server (the 27B type-2 model on vLLM) with --server.
Usage: m_decoder_zs.py NAME MODEL_ID [--max-len N] [--batch N] [--server URL --split test_core]
"""
import argparse
import concurrent.futures as cf
import math
import random
import string

import numpy as np
import requests
import torch

import common as C

SYSTEM = "You are a senior SOC analyst triaging Linux host and network logs from a small company."
LETTERS = string.ascii_uppercase[:len(C.TACTICS)]


def user_mal(text):
    return (f"Log window (times are relative to the first event; addresses are anonymized):\n{text}\n\n"
            f"Question: {C.MAL_QUESTION} Answer with yes or no.")


def user_tac(text):
    opts = "\n".join(f"{L}. {t}: {C.TACTIC_CRITERIA[t]}" for L, t in zip(LETTERS, C.TACTICS))
    return (f"Log window (times are relative to the first event; addresses are anonymized):\n{text}\n\n"
            f"Question: Which MITRE ATT&CK tactic best describes this activity?\n{opts}\nAnswer with the letter only.")


class Local:
    def __init__(self, model_id, max_len):
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.tok = AutoTokenizer.from_pretrained(model_id)
        self.tok.padding_side = "left"
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        if "Qwen3.5" in model_id:
            from transformers.models.qwen3_5 import Qwen3_5ForCausalLM as cls
        else:
            cls = AutoModelForCausalLM
        self.model = cls.from_pretrained(model_id, dtype=torch.bfloat16).cuda().eval()
        self.max_len = max_len
        self.yes = [self._tid(w) for w in ("yes", "Yes")]
        self.no = [self._tid(w) for w in ("no", "No")]
        self.letters = [self._tid(L) for L in LETTERS]

    def _tid(self, s):
        ids = self.tok.encode(s, add_special_tokens=False)
        assert len(ids) == 1, (s, ids)
        return ids[0]

    def prompt(self, user):
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        return self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)

    def fit(self, text, build):
        """Cut the window text from the end until the prompt fits max_len tokens."""
        p = self.prompt(build(text))
        n = len(self.tok(p)["input_ids"])
        if n <= self.max_len:
            return p
        lines = text.split("\n")
        while len(lines) > 2 and n > self.max_len:
            lines = lines[:-1]
            p = self.prompt(build("\n".join(lines)))
            n = len(self.tok(p)["input_ids"])
        return p

    @torch.no_grad()
    def next_logprobs(self, prompts, ids, bs):
        out = np.zeros((len(prompts), len(ids)))
        order = sorted(range(len(prompts)), key=lambda i: len(prompts[i]))
        for i in range(0, len(order), bs):
            idx = order[i:i + bs]
            enc = self.tok([prompts[j] for j in idx], return_tensors="pt", padding=True, add_special_tokens=False)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                lg = self.model(**{k: v.cuda() for k, v in enc.items()}, logits_to_keep=1).logits[:, -1].float()
            out[idx] = torch.log_softmax(lg, -1)[:, ids].cpu().numpy()
        return out

    def score(self, rows, bs):
        pm = [self.fit(r["text"], user_mal) for r in rows]
        pt = [self.fit(r["text"], user_tac) for r in rows]
        lp = self.next_logprobs(pm, self.yes + self.no, bs)
        yes = np.logaddexp(lp[:, 0], lp[:, 1])
        no = np.logaddexp(lp[:, 2], lp[:, 3])
        p = 1 / (1 + np.exp(no - yes))
        lt = self.next_logprobs(pt, self.letters, bs)
        tp = np.exp(lt - lt.max(1, keepdims=True))
        return p, tp / tp.sum(1, keepdims=True)


class Server:
    """OpenAI-compatible chat server (vLLM): one generated token with top logprobs."""

    def __init__(self, url, model, max_len):
        self.url, self.model, self.max_len = url.rstrip("/"), model, max_len

    def _one(self, user, want):
        body = {"model": self.model, "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 20,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                "chat_template_kwargs": {"enable_thinking": False}}
        r = requests.post(f"{self.url}/v1/chat/completions", json=body, timeout=300)
        r.raise_for_status()
        top = r.json()["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        got = {}
        for t in top:
            k = t["token"].strip().lower() if want == "yn" else t["token"].strip().upper()
            got[k] = np.logaddexp(got.get(k, -np.inf), t["logprob"])
        return got

    def score(self, rows, bs):
        def mal(r):
            g = self._one(user_mal(r["text"][:self.max_len * 3]), "yn")
            y, n = g.get("yes", -30.0), g.get("no", -30.0)
            return 1 / (1 + math.exp(n - y))

        def tac(r):
            g = self._one(user_tac(r["text"][:self.max_len * 3]), "letter")
            v = np.array([g.get(L, -30.0) for L in LETTERS])
            e = np.exp(v - v.max())
            return e / e.sum()
        with cf.ThreadPoolExecutor(bs) as ex:
            p = np.array(list(ex.map(mal, rows)))
            tp = np.stack(list(ex.map(tac, rows)))
        return p, tp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("model_id")
    ap.add_argument("--max-len", type=int, default=2048)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--server", default=None)
    ap.add_argument("--splits", default="test_core")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N windows of each split (smoke test)")
    a = ap.parse_args()
    sp = C.splits(C.load())
    scorer = Server(a.server, a.model_id, a.max_len) if a.server else Local(a.model_id, a.max_len)

    def cut(rows):   # seeded random subset; the kept windows keep their weights, so prevalence stays realistic
        return random.Random(0).sample(rows, a.limit) if a.limit and a.limit < len(rows) else rows
    val = cut(sp["val"])
    val_p, _ = scorer.score(val, a.batch)
    res, scores = {}, {}
    for split in a.splits.split(","):
        rows = cut(sp[split])
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t = C.Timer()
        p, tp = scorer.score(rows, a.batch)
        secs = t()
        res[split] = C.evaluate(rows, p, tp, val=(val, val_p))
        res[split]["ms_per_window_batched_2q"] = 1000 * secs / len(rows)
        scores[split] = p
    info = {"model_id": a.model_id, "zero_shot": True, "max_len": a.max_len, "server": bool(a.server),
            "peak_vram_gb": None if a.server else C.gpu_mem()}
    C.save(a.name, info, res, scores)


if __name__ == "__main__":
    main()
