"""type-1 scoring server: POST /score, GET /health (the API type1-triage's scorer client calls).

Serves one model directory on the GPU. The directory holds a `type1.json` naming its backend:
    {"backend": "seqcls", "model": "<dir or hub id>", "adapter": "<peft dir, optional>", "temperature": 1.3,
     "max_len": 1024, "name": "..."}
    {"backend": "laya", "model": "<laya checkpoint dir>", "max_len": 1024, "name": "..."}
Binds to 127.0.0.1 only (the AI box has no host firewall). Stdlib HTTP server: the client already batches.
Usage: type1_serve.py MODEL_DIR [--port 8090]
"""
import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch

sys.path.insert(0, "/opt/llm/type1")
sys.path.insert(0, "/opt/llm/type1/bench")
from type1canon import canon  # noqa: E402
import common as C  # noqa: E402

MAX_BATCH = 256


class SeqCls:
    def __init__(self, cfg):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        base = cfg["model"]
        if "Qwen3.5" in base:
            from transformers.models.qwen3_5 import Qwen3_5TextForSequenceClassification as cls
        else:
            cls = AutoModelForSequenceClassification
        self.model = cls.from_pretrained(base, num_labels=len(C.TACTICS), dtype=torch.bfloat16)
        self.tok = AutoTokenizer.from_pretrained(cfg.get("adapter") or base)
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        self.model.config.pad_token_id = self.tok.pad_token_id
        if cfg.get("adapter"):
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, cfg["adapter"]).merge_and_unload()
        self.model.cuda().eval()
        self.T, self.max_len = float(cfg.get("temperature", 1.0)), int(cfg.get("max_len", 1024))

    @torch.no_grad()
    def __call__(self, texts):
        enc = self.tok(texts, truncation=True, max_length=self.max_len, padding=True, return_tensors="pt")
        with torch.autocast("cuda", dtype=torch.bfloat16):
            lg = self.model(**{k: v.cuda() for k, v in enc.items()}).logits.float()
        return torch.softmax(lg / self.T, -1).cpu().numpy()


class Laya:
    def __init__(self, cfg):
        import laya
        from m_laya import HEAD_MAX_LEN, QUESTIONS
        self.agent, self.q, self.head = laya.load(cfg["model"], device="cuda"), QUESTIONS, HEAD_MAX_LEN
        self.max_len = int(cfg.get("max_len", 1024))

    def __call__(self, texts):
        res = self.agent.predict_batch(texts, self.q, batch_size=16, max_len=self.max_len, head_max_len=self.head)
        out = np.zeros((len(texts), len(C.TACTICS)))
        for i, r in enumerate(res):
            probs = r["answers"]["tactic"]["probabilities"]
            p_mal = r["answers"]["malicious"]["noul"]
            attack = np.array([probs.get(t, 0.0) for t in C.TACTICS[1:]])
            out[i, 0] = 1 - p_mal     # the malicious question decides; the tactic question splits the rest
            out[i, 1:] = p_mal * attack / max(attack.sum(), 1e-9)
        return out


BACKENDS = {"seqcls": SeqCls, "laya": Laya}


def make_handler(model, name, lock):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"model": name, "render_version": canon.RENDER_VERSION})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/score":
                return self._send(404, {"error": "not found"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                wins = req["windows"]
                assert isinstance(wins, list) and len(wins) <= MAX_BATCH
                texts = [str(w["text"]) for w in wins]
            except (ValueError, KeyError, TypeError, AssertionError):
                return self._send(400, {"error": f"expected {{windows: [{{id, text}}]}}, at most {MAX_BATCH}"})
            with lock:     # one batch on the GPU at a time
                probs = model(texts) if texts else np.zeros((0, len(C.TACTICS)))
            results = []
            for w, p in zip(wins, probs):
                k = int(np.argmax(p[1:])) + 1
                results.append({"id": w.get("id"), "p_malicious": round(float(1 - p[0]), 6),
                                "tactic": C.TACTICS[k] if p[0] < 0.5 else "Benign",
                                "tactic_probs": {t: round(float(x), 6) for t, x in zip(C.TACTICS, p)}})
            self._send(200, {"model": name, "render_version": canon.RENDER_VERSION, "results": results})

        def log_message(self, *a):
            pass
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model_dir")
    ap.add_argument("--port", type=int, default=8090)
    a = ap.parse_args()
    cfg = json.load(open(f"{a.model_dir}/type1.json"))
    model = BACKENDS[cfg["backend"]](cfg)
    name = cfg.get("name", a.model_dir)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(model, name, threading.Lock()))
    print(f"type1-serve {name} on 127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
