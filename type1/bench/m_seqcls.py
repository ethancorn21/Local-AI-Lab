"""Fine-tune a transformer as a window classifier: one softmax over TACTICS (Benign + attack tactics).

p(malicious) = 1 - p(Benign); the tactic is the argmax over the attack tactics. Encoders are fine-tuned in full;
decoders (Qwen3.5) get LoRA adapters plus a trained score head. The checkpoint with the best val loss is kept, then
one temperature is fitted on val so the probabilities are calibrated.

Usage: m_seqcls.py NAME MODEL_ID [--lora] [--max-len N] [--lr X] [--epochs N] [--batch N] [--train-cap N]
"""
import argparse
import math
import os
import random

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModelForSequenceClassification, AutoTokenizer

import common as C


def load_model(model_id, lora, n):
    kw = {"num_labels": n, "dtype": torch.bfloat16}
    if "Qwen3.5" in model_id:
        from transformers.models.qwen3_5 import Qwen3_5TextForSequenceClassification as cls
        model = cls.from_pretrained(model_id, **kw)
    else:
        model = AutoModelForSequenceClassification.from_pretrained(model_id, **kw)
    tok = AutoTokenizer.from_pretrained(model_id)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model.config.pad_token_id = tok.pad_token_id
    if lora:
        from peft import LoraConfig, get_peft_model
        cfg = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="SEQ_CLS",
                         target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
        model = get_peft_model(model, cfg)
        model.print_trainable_parameters()
    return model, tok


def batches(rows, tok, max_len, bs, shuffle):
    idx = list(range(len(rows)))
    if shuffle:
        random.shuffle(idx)
    else:   # sort by length so padding is small
        idx.sort(key=lambda i: len(rows[i]["text"]))
    for i in range(0, len(idx), bs):
        chunk = [rows[j] for j in idx[i:i + bs]]
        enc = tok([r["text"] for r in chunk], truncation=True, max_length=max_len, padding=True, return_tensors="pt")
        yield idx[i:i + bs], enc, torch.tensor([C.TACTICS.index(r["tactic"]) for r in chunk])


@torch.no_grad()
def logits_for(model, tok, rows, max_len, bs):
    model.eval()
    out = np.zeros((len(rows), len(C.TACTICS)), np.float32)
    for ids, enc, _ in batches(rows, tok, max_len, bs, shuffle=False):
        with torch.autocast("cuda", dtype=torch.bfloat16):
            lg = model(**{k: v.cuda() for k, v in enc.items()}).logits.float()
        out[ids] = lg.cpu().numpy()
    model.train()
    return out


def fit_temperature(logits, gold):
    z, g = torch.tensor(logits), torch.tensor(gold)
    best = min(np.exp(np.linspace(np.log(0.3), np.log(5), 80)),
               key=lambda T: F.cross_entropy(z / T, g).item())
    return float(best)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("model_id")
    ap.add_argument("--lora", action="store_true")
    ap.add_argument("--max-len", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--micro", type=int, default=4)
    ap.add_argument("--train-cap", type=int, default=300)
    ap.add_argument("--exclude-family", default=None, help="leave-one-attack-out: drop this family from train/val")
    ap.add_argument("--no-full", action="store_true", help="skip test_full")
    a = ap.parse_args()
    random.seed(0)
    torch.manual_seed(0)
    sp = C.splits(C.load(), train_cap=a.train_cap)
    tr, va = sp["train"], sp["val"]
    if a.exclude_family:
        tr = [r for r in tr if r["family"] != a.exclude_family]
        va = [r for r in va if r["family"] != a.exclude_family]
    model, tok = load_model(a.model_id, a.lora, len(C.TACTICS))
    model.cuda()
    if hasattr(model, "gradient_checkpointing_enable"):
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        if a.lora:
            model.enable_input_require_grads()
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.01)
    steps = math.ceil(len(tr) / a.batch) * a.epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=int(steps) + 1, pct_start=0.06)
    gold_va = np.array([C.TACTICS.index(r["tactic"]) for r in va])
    eval_every = max(1, math.ceil(len(tr) / a.batch) // 3)
    best, best_state, step, t = float("inf"), None, 0, C.Timer()
    model.train()
    done = False
    for ep in range(math.ceil(a.epochs)):
        for ids, enc, y in batches(tr, tok, a.max_len, a.batch, shuffle=True):
            opt.zero_grad()
            for j in range(0, len(ids), a.micro):
                sub = {k: v[j:j + a.micro].cuda() for k, v in enc.items()}
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    lg = model(**sub).logits.float()
                loss = F.cross_entropy(lg, y[j:j + a.micro].cuda()) * len(sub["input_ids"]) / len(ids)
                loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            sched.step()
            step += 1
            if step % 25 == 0:
                print(f"step {step}/{int(steps)} loss={loss.item() * len(ids) / min(a.micro, len(ids)):.4f} "
                      f"mem={C.gpu_mem()}GB {t():.0f}s", flush=True)
            if step % eval_every == 0 or step >= steps:
                vl = logits_for(model, tok, va, a.max_len, a.batch)
                loss_va = F.cross_entropy(torch.tensor(vl), torch.tensor(gold_va)).item()
                print(f"val loss {loss_va:.4f} at step {step}", flush=True)
                if loss_va < best:   # with LoRA only the adapters and the head change
                    best = loss_va
                    best_state = {k: v.detach().to("cpu", copy=True) for k, v in model.state_dict().items()
                                  if not a.lora or "lora" in k or "score" in k}
            if step >= steps:
                done = True
                break
        if done:
            break
    train_s = t()
    model.load_state_dict(best_state, strict=False)
    vl = logits_for(model, tok, va, a.max_len, a.batch)
    T = fit_temperature(vl, gold_va)
    val_p = 1 - torch.softmax(torch.tensor(vl) / T, -1)[:, 0].numpy()
    res, scores = {}, {}
    for name in ["test_core"] + ([] if a.no_full else ["test_full"]):
        rows = sp[name]
        torch.cuda.synchronize()
        t = C.Timer()
        lg = logits_for(model, tok, rows, a.max_len, a.batch)
        secs = t()
        probs = torch.softmax(torch.tensor(lg) / T, -1).numpy()
        p = 1 - probs[:, 0]
        res[name] = C.evaluate(rows, p, probs, val=(va, val_p))
        res[name]["ms_per_window_batched"] = 1000 * secs / len(rows)
        scores[name] = p
    # single-window latency (batch 1), the live service's worst case
    one = sp["test_core"][:64]
    torch.cuda.synchronize()
    t = C.Timer()
    for r in one:
        logits_for(model, tok, [r], a.max_len, 1)
    torch.cuda.synchronize()
    lens = [len(tok(r["text"])["input_ids"]) for r in sp["test_core"][:2000]]
    info = {"model_id": a.model_id, "lora": a.lora, "max_len": a.max_len, "lr": a.lr, "epochs": a.epochs,
            "train_windows": len(tr), "exclude_family": a.exclude_family, "best_val_loss": best,
            "temperature": T, "train_seconds": train_s, "ms_per_window_bs1": 1000 * t() / len(one),
            "peak_vram_gb": C.gpu_mem(), "truncated_frac": float(np.mean(np.array(lens) > a.max_len)),
            "tokens_p50_p95": [int(np.percentile(lens, 50)), int(np.percentile(lens, 95))]}
    C.save(a.name, info, res, scores)
    if os.environ.get("SAVE_MODEL"):
        out = f"{C.ROOT}/models/{a.name}"
        model.save_pretrained(out)
        tok.save_pretrained(out)
        with open(f"{out}/temperature.txt", "w") as f:
            f.write(str(T))


if __name__ == "__main__":
    main()
