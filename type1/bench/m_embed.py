"""Frozen embedding model + logistic regression: embed each window once, train only a linear classifier on top.

Cheap to train and to retrain as labels change; the embedding model itself never sees the labels.
Usage: m_embed.py NAME MODEL_ID [--max-len N] [--batch N]
"""
import argparse

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.linear_model import LogisticRegression
from transformers import AutoModel, AutoTokenizer

import common as C

INSTRUCT = "Instruct: Decide whether this window of Linux or network log events shows attacker activity\nQuery: "


@torch.no_grad()
def embed(model, tok, rows, max_len, bs):
    out = np.zeros((len(rows), model.config.hidden_size), np.float32)
    order = sorted(range(len(rows)), key=lambda i: len(rows[i]["text"]))
    for i in range(0, len(order), bs):
        idx = order[i:i + bs]
        enc = tok([INSTRUCT + rows[j]["text"] for j in idx], truncation=True, max_length=max_len, padding=True,
                  return_tensors="pt")
        with torch.autocast("cuda", dtype=torch.bfloat16):
            h = model(**{k: v.cuda() for k, v in enc.items()}).last_hidden_state
        h = h[:, -1]            # left padding: the last position is every sequence's last token
        out[idx] = F.normalize(h.float(), dim=-1).cpu().numpy()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("model_id")
    ap.add_argument("--max-len", type=int, default=2048)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--no-full", action="store_true", help="skip test_full (127k windows)")
    a = ap.parse_args()
    sp = C.splits(C.load())
    tok = AutoTokenizer.from_pretrained(a.model_id, padding_side="left")
    model = AutoModel.from_pretrained(a.model_id, dtype=torch.bfloat16).cuda().eval()
    t = C.Timer()
    E = {k: embed(model, tok, sp[k], a.max_len, a.batch) for k in ("train", "val")}
    embed_s = t()
    ytr = np.array([r["malicious"] for r in sp["train"]])
    ttr = np.array([C.TACTICS.index(r["tactic"]) for r in sp["train"]])
    best = None
    for c in (0.3, 1, 3, 10, 30):
        m = LogisticRegression(C=c, max_iter=5000, class_weight="balanced").fit(E["train"], ytr)
        auc = C.auroc(np.array([r["malicious"] for r in sp["val"]], float), m.predict_proba(E["val"])[:, 1],
                      np.array([r["w"] for r in sp["val"]]))
        if best is None or auc > best[0]:
            best = (auc, c, m)
    _, c, mal = best
    tac = LogisticRegression(C=c, max_iter=5000, class_weight="balanced").fit(E["train"], ttr)
    val_p = mal.predict_proba(E["val"])[:, 1]
    res, scores = {}, {}
    for split in ["test_core"] + ([] if a.no_full else ["test_full"]):
        rows = sp[split]
        torch.cuda.synchronize()
        t = C.Timer()
        X = embed(model, tok, rows, a.max_len, a.batch)
        p = mal.predict_proba(X)[:, 1]
        tp = np.zeros((len(rows), len(C.TACTICS)))
        tp[:, tac.classes_] = tac.predict_proba(X)
        secs = t()
        res[split] = C.evaluate(rows, p, tp, val=(sp["val"], val_p))
        res[split]["ms_per_window_batched"] = 1000 * secs / len(rows)
        scores[split] = p
    info = {"model_id": a.model_id, "max_len": a.max_len, "C": c, "embed_train_val_seconds": embed_s,
            "train_windows": len(sp["train"]), "peak_vram_gb": C.gpu_mem()}
    C.save(a.name, info, res, scores)


if __name__ == "__main__":
    main()
