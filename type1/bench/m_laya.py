"""Laya (convaiinnovations/laya, ModernBERT-large + RLCD decision head): zero-shot, and fine-tuned on TRAIN.

Fine-tuning follows the old openjev-lab recipe (finetune_v3.py): cross-entropy on the answer options only, act head
frozen, tactic options shuffled per example, best val-loss checkpoint, one temperature per question bucket.
Usage: m_laya.py zs NAME [--subfolder multilingual] [--max-len N]
       m_laya.py ft NAME [--epochs N] [--train-cap N] [--exclude-family F]
"""
import argparse
import copy
import json
import math
import random
import shutil
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from safetensors.torch import save_file

import laya
from laya.common import build_sequence, collate_items, temp_bucket

import common as C

HEAD_MAX_LEN = 320
QUESTIONS = {
    "malicious": {"type": "noul", "instructions": C.MAL_QUESTION},
    "tactic": {"type": "choice", "instructions": "Which MITRE ATT&CK tactic best describes this activity?",
               "criteria": C.TACTIC_CRITERIA},
}


def predict(agent, rows, max_len, bs=16):
    res = agent.predict_batch([r["text"] for r in rows], QUESTIONS, batch_size=bs, max_len=max_len,
                              head_max_len=HEAD_MAX_LEN, sort_by_length=True)
    p = np.array([x["answers"]["malicious"]["noul"] for x in res])
    tp = np.zeros((len(rows), len(C.TACTICS)))
    for i, x in enumerate(res):
        probs = x["answers"]["tactic"].get("probabilities") or x["answers"]["tactic"].get("probs") or {}
        if probs:
            for k, v in probs.items():
                if k in C.TACTICS:
                    tp[i, C.TACTICS.index(k)] = v
        else:
            tp[i, C.TACTICS.index(x["answers"]["tactic"]["choice"])] = 1.0
    return p, tp


def score_all(agent, sp, max_len, name, info, skip_full=False):
    val_p, _ = predict(agent, sp["val"], max_len)
    res, scores = {}, {}
    for split in ["test_core"] + ([] if skip_full else ["test_full"]):
        rows = sp[split]
        torch.cuda.synchronize()
        t = C.Timer()
        p, tp = predict(agent, rows, max_len)
        secs = t()
        res[split] = C.evaluate(rows, p, tp, val=(sp["val"], val_p))
        res[split]["ms_per_window_batched"] = 1000 * secs / len(rows)
        scores[split] = p
    one = sp["test_core"][:64]
    t = C.Timer()
    for r in one:
        predict(agent, [r], max_len, bs=1)
    info.update({"ms_per_window_bs1": 1000 * t() / len(one), "peak_vram_gb": C.gpu_mem(), "max_len": max_len})
    C.save(name, info, res, scores)


def encode(agent, row, internal, shuffle, max_len):
    tok, items = agent.tok, []
    ids, markers = build_sequence(tok, row["text"], internal["malicious"], max_len, HEAD_MAX_LEN)
    items.append({"ids": ids, "markers": markers, "qtype": 2,
                  "target": [1.0 - row["malicious"], float(row["malicious"])]})
    order = list(range(len(C.TACTICS)))
    if shuffle:
        random.shuffle(order)
    ids, markers = build_sequence(tok, row["text"], internal["tactic"], max_len, HEAD_MAX_LEN, option_order=order)
    if len(markers) == len(C.TACTICS):
        gold = C.TACTICS.index(row["tactic"])
        items.append({"ids": ids, "markers": markers, "qtype": 0, "target": [float(o == gold) for o in order]})
    return items


def run(agent, groups):
    items = [it for g in groups for it in g]
    b = collate_items(groups, agent.tok.pad_token_id)
    dev = agent.device
    with torch.autocast("cuda", dtype=torch.bfloat16):
        logits, _ = agent.model(b["input_ids"].to(dev), b["attention_mask"].to(dev), b["marker_pos"].to(dev),
                                b["marker_mask"].to(dev), b["qtype"].to(dev))
    logits = logits.float()
    loss = -(b["target"].to(dev) * F.log_softmax(logits, -1)).sum(-1).mean()
    return loss, logits.detach(), items


def finetune(a, sp):
    random.seed(0)
    torch.manual_seed(0)
    tr, va = sp["train"], sp["val"]
    if a.exclude_family:
        tr = [r for r in tr if r["family"] != a.exclude_family]
        va = [r for r in va if r["family"] != a.exclude_family]
        sp = dict(sp, val=va)
    agent = laya.load("convaiinnovations/laya", device="cuda")
    internal = {k: agent._to_internal(v) for k, v in QUESTIONS.items()}
    va_enc = [encode(agent, r, internal, False, a.max_len) for r in va]
    model = agent.model
    model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.head_checkpointing = True
    for p in model.act_head.parameters():
        p.requires_grad_(False)
    params = [p for p in model.parameters() if p.requires_grad]
    lr, batch, micro = 2e-5, 16, 2
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=0.01)
    n_batches = math.ceil(len(tr) / batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=a.epochs * n_batches, pct_start=0.06)

    def val_loss():
        model.eval()
        tot = 0.0
        with torch.no_grad():
            for i in range(0, len(va_enc), 4):
                tot += run(agent, va_enc[i:i + 4])[0].item() * len(va_enc[i:i + 4])
        model.train()
        return tot / len(va_enc)

    best, best_state, t = float("inf"), None, C.Timer()
    model.train()
    for ep in range(a.epochs):
        random.shuffle(tr)
        for bi, i in enumerate(range(0, len(tr), batch)):
            chunk = tr[i:i + batch]
            opt.zero_grad()
            for j in range(0, len(chunk), micro):
                enc = [encode(agent, r, internal, True, a.max_len) for r in chunk[j:j + micro]]
                loss = run(agent, enc)[0]
                (loss * len(enc) / len(chunk)).backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            sched.step()
            if bi % 50 == 0:
                print(f"ep {ep + 1} batch {bi}/{n_batches} loss={loss.item():.4f} mem={C.gpu_mem()}GB {t():.0f}s",
                      flush=True)
            if (bi + 1) % max(1, n_batches // 3) == 0 or bi + 1 == n_batches:
                vl = val_loss()
                print(f"val loss {vl:.4f}", flush=True)
                if vl < best:
                    best = vl
                    best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    train_s = t()
    model.load_state_dict(best_state)
    model.eval()
    buckets = {}
    with torch.no_grad():
        for i in range(0, len(va_enc), 4):
            _, logits, items = run(agent, va_enc[i:i + 4])
            for row, it in zip(logits.cpu().numpy(), items):
                k = len(it["markers"])
                buckets.setdefault(temp_bucket(it["qtype"], k), []).append((row[:k], np.array(it["target"])))
    grid = np.exp(np.linspace(np.log(0.3), np.log(5.0), 120))
    temps = {}
    for bucket, pairs in buckets.items():
        z, tgt = np.stack([p[0] for p in pairs]), np.stack([p[1] for p in pairs])

        def nll(T):
            zt = z / T
            m = zt.max(1, keepdims=True)
            return -np.mean(((zt - (m + np.log(np.exp(zt - m).sum(1, keepdims=True)))) * tgt).sum(1))
        temps[bucket] = float(min(grid, key=nll))
    from huggingface_hub import snapshot_download
    src = Path(snapshot_download("convaiinnovations/laya", allow_patterns=["rl_agent_config.json", "tokenizer/*",
                                                                           "encoder/*"]))
    out = Path(C.ROOT) / "models" / a.name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for sub in ("tokenizer", "encoder"):
        shutil.copytree(src / sub, out / sub)
    cfg = copy.deepcopy(agent.cfg)
    cfg["temperature_by_options"] = {**cfg.get("temperature_by_options", {}), **temps}
    cfg["training"] = {"fine_tuned_from": "convaiinnovations/laya", "task": "type1-ait-v1", "epochs": a.epochs,
                       "train_windows": len(tr), "best_val_loss": best, "exclude_family": a.exclude_family}
    (out / "rl_agent_config.json").write_text(json.dumps(cfg, indent=2))
    save_file({k: v.contiguous() for k, v in best_state.items()}, str(out / "model.safetensors"))
    del agent, model, opt
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    return laya.load(str(out), device="cuda"), sp, {"train_windows": len(tr), "train_seconds": train_s,
                                                     "temperatures": temps, "exclude_family": a.exclude_family}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["zs", "ft"])
    ap.add_argument("name")
    ap.add_argument("--subfolder", default=None)
    ap.add_argument("--max-len", type=int, default=1024)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--train-cap", type=int, default=300)
    ap.add_argument("--exclude-family", default=None)
    ap.add_argument("--no-full", action="store_true")
    a = ap.parse_args()
    sp = C.splits(C.load(), train_cap=a.train_cap)
    if a.mode == "zs":
        agent = laya.load("convaiinnovations/laya", device="cuda", subfolder=a.subfolder)
        info = {"model": "convaiinnovations/laya" + (f"/{a.subfolder}" if a.subfolder else ""), "zero_shot": True}
    else:
        agent, sp, info = finetune(a, sp)
    score_all(agent, sp, a.max_len, a.name, info, skip_full=a.no_full)


if __name__ == "__main__":
    main()
