"""Summarise results/*.json into one markdown table (test_core unless another split is named).
Usage: report.py [split] [results_dir]
"""
import glob
import json
import os
import sys

split = sys.argv[1] if len(sys.argv) > 1 else "test_core"
rdir = sys.argv[2] if len(sys.argv) > 2 else "/opt/llm/type1/results"
rows = []
for f in sorted(glob.glob(f"{rdir}/*.json")):
    d = json.load(open(f))
    r = d["results"].get(split)
    if not r:
        continue
    op = r.get("val_thr@fpr0.01", {})
    fam = r.get("family_recall@val_fpr0.01", {})
    ms = r.get("ms_per_window_batched") or r.get("ms_per_window_batched_2q") or r.get("ms_per_window_cpu")
    rows.append((r["auroc"], [
        d["method"], f'{r["auroc"]:.4f}', f'{r["ap"]:.4f}', f'{r["recall@fpr0.01"]:.3f}', f'{r["recall@fpr0.001"]:.3f}',
        f'{op.get("test_recall", float("nan")):.3f} / {100 * op.get("test_fpr", float("nan")):.2f}%',
        " ".join(f"{k[:4]}={v:.2f}" for k, v in fam.items()),
        f'{r.get("tactic_acc_on_malicious", float("nan")):.3f}', f'{r["ece"]:.3f}',
        f"{ms:.1f}" if ms else "", f'{d.get("ms_per_window_bs1", float("nan")):.1f}', str(d.get("peak_vram_gb", ""))]))
head = ["method", "AUROC", "AP", "rec@1%FPR", "rec@0.1%FPR", "val-thr rec / FPR", "family recall @val-thr",
        "tactic acc", "ECE", "ms/win batch", "ms/win bs1", "VRAM GB"]
print(f"split: {split} ({os.path.basename(rdir)})\n")
print("| " + " | ".join(head) + " |")
print("|" + "---|" * len(head))
for _, cells in sorted(rows, key=lambda x: -x[0]):
    print("| " + " | ".join(cells) + " |")
