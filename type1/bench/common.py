"""Shared pieces of the type-1 bake-off: questions, splits, sampling, metrics, result files.

Every method is trained (if it trains) on TRAIN, tunes and calibrates on VAL, and is scored on the same TEST windows.
Splits are whole testbeds (separate simulated companies), fixed before any result was seen, so a method cannot win
by memorising one environment.
"""
import collections
import json
import os
import random
import time

import numpy as np

ROOT = "/opt/llm/type1"
DATA = f"{ROOT}/data/windows_ait_v2.jsonl"
RESULTS = f"{ROOT}/results"

TRAIN = ["russellmitchell", "fox", "wardbeck", "shaw", "wheeler"]
VAL = ["harrison"]
TEST = ["santos", "wilson"]

TACTICS = ["Benign", "Reconnaissance", "Initial Access", "Execution", "Persistence", "Privilege Escalation",
           "Stealth", "Defense Impairment", "Credential Access", "Discovery", "Lateral Movement", "Collection",
           "Command and Control", "Exfiltration", "Impact"]
TACTIC_CRITERIA = {
    "Benign": "normal system, service, user or network activity",
    "Reconnaissance": "scanning or probing from outside before any foothold",
    "Initial Access": "first foothold: exploited public-facing apps, uploaded webshells, stolen VPN or SSH logins",
    "Execution": "running attacker commands or code: webshell commands, scripts, interpreters",
    "Persistence": "cron jobs, services, SSH keys, new accounts, startup scripts",
    "Privilege Escalation": "gaining root or another user: su/sudo abuse, exploits, SUID",
    "Stealth": "hiding activity: masquerading, obfuscation, hidden files",
    "Defense Impairment": "disabling security tools, tampering with or clearing logs",
    "Credential Access": "reading /etc/shadow, password cracking, brute force, credential dumping",
    "Discovery": "internal scans, directory brute forcing, enumerating hosts, users, services",
    "Lateral Movement": "moving to other hosts over SSH, SMB or remote services",
    "Collection": "staging, archiving or gathering data of interest",
    "Command and Control": "beacons, tunnels, reverse shells, remote access tools",
    "Exfiltration": "sending data out: DNS tunnelling, uploads, unusual outbound transfers",
    "Impact": "destroying or encrypting data, stopping services",
}
MAL_QUESTION = "Do these log events show malicious or attacker activity?"


def load(path=DATA):
    return [json.loads(line) for line in open(path)]


def _cap(rows, key, cap, seed):
    rng = random.Random(seed)
    groups = collections.defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    out = []
    for k in sorted(groups):
        g = groups[k]
        rng.shuffle(g)
        kept = g[:cap]
        for r in kept:   # each kept window stands for this many of its group (realistic prevalence in metrics)
            r = dict(r)
            r["w"] = r.get("weight", 1.0) * len(g) / len(kept)
            out.append(r)
    return out


def group_key(r):
    return (r["testbed"], r["source"], r["family"])


def splits(rows, train_cap=300, val_cap=300, core_cap=150, seed=7):
    """train/val: capped per (testbed, source, family); test_core: the same stratified sample for every method;
    test_full: every kept test window (for the fast methods)."""
    by = collections.defaultdict(list)
    for r in rows:
        by["train" if r["testbed"] in TRAIN else "val" if r["testbed"] in VAL else "test"].append(r)
    full = [dict(r, w=r.get("weight", 1.0)) for r in by["test"]]
    return {
        "train": _cap(by["train"], group_key, train_cap, seed),
        "val": _cap(by["val"], group_key, val_cap, seed),
        "test_core": _cap(by["test"], group_key, core_cap, seed),
        "test_full": full,
    }


# ---------- metrics ----------

def _roc(y, s, w):
    order = np.argsort(-s, kind="mergesort")
    y, s, w = y[order], s[order], w[order]
    tp = np.cumsum(w * y)
    fp = np.cumsum(w * (1 - y))
    last = np.r_[np.where(np.diff(s))[0], len(s) - 1]     # one point per distinct score
    tpr = tp[last] / max(tp[-1], 1e-12)
    fpr = fp[last] / max(fp[-1], 1e-12)
    return np.r_[0, fpr], np.r_[0, tpr], np.r_[np.inf, s[last]], tp[last], fp[last]


def auroc(y, s, w):
    fpr, tpr, *_ = _roc(y, s, w)
    return float(np.trapezoid(tpr, fpr))


def average_precision(y, s, w):
    _, tpr, _, tp, fp = _roc(y, s, w)
    prec = tp / np.maximum(tp + fp, 1e-12)
    return float(np.sum(np.diff(tpr) * prec))


def threshold_at_fpr(y, s, w, target):
    """Lowest threshold whose (weighted) false-positive rate stays at or below target."""
    fpr, _, thr, *_ = _roc(y, s, w)
    ok = np.where(fpr <= target)[0]
    return float(thr[ok[-1]]) if len(ok) else float("inf")


def rates(y, s, w, thr):
    flag = s >= thr
    tpr = float((w * y * flag).sum() / max((w * y).sum(), 1e-12))
    fpr = float((w * (1 - y) * flag).sum() / max((w * (1 - y)).sum(), 1e-12))
    return tpr, fpr


def ece(y, p, w, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    tot, err = w.sum(), 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            err += w[m].sum() / tot * abs((w[m] * y[m]).sum() / w[m].sum() - (w[m] * p[m]).sum() / w[m].sum())
    return float(err)


def evaluate(rows, p_mal, tactic_probs=None, val=None):
    """rows: window dicts with malicious/family/tactic/w. p_mal: array of p(malicious). tactic_probs: optional
    (n, len(TACTICS)) array. val: optional (rows, p_mal) used to pick the operating thresholds."""
    y = np.array([r["malicious"] for r in rows], float)
    w = np.array([r["w"] for r in rows], float)
    s = np.asarray(p_mal, float)
    out = {"n": len(rows), "n_malicious": int(y.sum()), "auroc": auroc(y, s, w), "ap": average_precision(y, s, w),
           "brier": float((w * (s - y) ** 2).sum() / w.sum()), "ece": ece(y, s, w)}
    for target in (0.01, 0.001):
        key = f"{target:g}"
        thr_test = threshold_at_fpr(y, s, w, target)
        out[f"recall@fpr{key}"] = rates(y, s, w, thr_test)[0]
        if val is not None:
            vr, vp = val
            vy = np.array([r["malicious"] for r in vr], float)
            vw = np.array([r["w"] for r in vr], float)
            thr = threshold_at_fpr(vy, np.asarray(vp, float), vw, target)
            tpr, fpr = rates(y, s, w, thr)
            out[f"val_thr@fpr{key}"] = {"threshold": thr, "test_recall": tpr, "test_fpr": fpr}
            if target == 0.01:   # per-family recall at the operating point picked on val
                fam = collections.defaultdict(lambda: [0, 0])
                for r, sc in zip(rows, s):
                    if r["malicious"]:
                        fam[r["family"]][0] += sc >= thr
                        fam[r["family"]][1] += 1
                out["family_recall@val_fpr0.01"] = {k: round(a / b, 4) for k, (a, b) in sorted(fam.items())}
                src = collections.defaultdict(lambda: [0.0, 0.0])
                for r, sc in zip(rows, s):
                    if not r["malicious"]:
                        src[r["source"]][0] += r["w"] * (sc >= thr)
                        src[r["source"]][1] += r["w"]
                out["source_fpr@val_fpr0.01"] = {k: round(a / b, 5) for k, (a, b) in sorted(src.items())}
    if tactic_probs is not None:
        tp = np.asarray(tactic_probs, float)
        mal = [i for i, r in enumerate(rows) if r["malicious"]]
        if mal:
            pred = tp[mal, 1:].argmax(1) + 1          # tactic among the attack tactics, given it is an attack
            gold = np.array([TACTICS.index(rows[i]["tactic"]) for i in mal])
            out["tactic_acc_on_malicious"] = float((pred == gold).mean())
            per = collections.defaultdict(lambda: [0, 0])
            for g, p in zip(gold, pred):
                per[TACTICS[g]][0] += int(g == p)
                per[TACTICS[g]][1] += 1
            out["tactic_acc_per_class"] = {k: round(a / b, 4) for k, (a, b) in sorted(per.items())}
    return out


# ---------- timing and results ----------

class Timer:
    def __init__(self):
        self.t = time.perf_counter()

    def __call__(self):
        return time.perf_counter() - self.t


def save(method, info, results, scores=None):
    os.makedirs(RESULTS, exist_ok=True)
    out = {"method": method, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **info, "results": results}
    with open(f"{RESULTS}/{method}.json", "w") as f:
        json.dump(out, f, indent=1)
    if scores is not None:
        np.savez_compressed(f"{RESULTS}/{method}.scores.npz", **scores)
    brief = {k: {m: round(v, 4) for m, v in r.items() if isinstance(v, float)} for k, r in results.items()}
    print(json.dumps({"method": method, **brief}, indent=1), flush=True)


def gpu_mem():
    try:
        import torch
        return round(torch.cuda.max_memory_allocated() / 2**30, 2)
    except Exception:
        return None
