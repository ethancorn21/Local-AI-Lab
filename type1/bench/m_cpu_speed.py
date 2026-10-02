"""CPU-only speed of the saved type-1 classifier (Qwen3.5-0.8B LoRA, merged), on real test_core windows.
Run pinned to the E-cores with no GPU visible, e.g.:
  CUDA_VISIBLE_DEVICES= taskset -c 16-31 chrt -i 0 .venv/bin/python bench/m_cpu_speed.py
Aborts if any core reaches --max-temp (the box's hwtemps guard stops all GPU servers at 95 C for 1 min)."""
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("HF_HOME", "/opt/llm/type1/hf")
os.environ["HF_HUB_OFFLINE"] = "1"
import argparse, glob, json, random, sys, time
# fla / causal_conv1d are Triton/CUDA-only; hide them so transformers takes the torch path for the DeltaNet layers
sys.modules["fla"] = sys.modules["causal_conv1d"] = None
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C


def hottest_core():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if open(f"{d}/name").read().strip() == "coretemp":
                return max(int(open(f).read()) for f in glob.glob(f"{d}/temp*_input")) / 1000
        except OSError:
            pass
    return 0.0


class TooHot(Exception):
    pass


def load(cfg, dtype):
    from transformers import AutoTokenizer
    from transformers.models.qwen3_5 import Qwen3_5TextForSequenceClassification as cls
    from peft import PeftModel
    model = cls.from_pretrained(cfg["model"], num_labels=len(C.TACTICS), dtype=dtype)
    tok = AutoTokenizer.from_pretrained(cfg["adapter"])
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model.config.pad_token_id = tok.pad_token_id
    model = PeftModel.from_pretrained(model, cfg["adapter"]).merge_and_unload()
    return model.eval(), tok


def run(model, tok, rows, bs, max_len, max_temp, peak):
    """Length-sorted batches (live service would batch the same way). Returns per-batch (n, tokens, secs) + logits."""
    order = sorted(range(len(rows)), key=lambda i: len(rows[i]["text"]))
    out = np.zeros((len(rows), len(C.TACTICS)), np.float32)
    stats = []
    for s in range(0, len(order), bs):
        ids = order[s:s + bs]
        enc = tok([rows[i]["text"] for i in ids], truncation=True, max_length=max_len, padding=True, return_tensors="pt")
        t = time.perf_counter()
        with torch.inference_mode():
            lg = model(**enc).logits.float()
        stats.append((len(ids), int(enc["attention_mask"].sum()), time.perf_counter() - t))
        out[ids] = lg.numpy()
        temp = hottest_core()
        peak[0] = max(peak[0], temp)
        if temp >= max_temp:
            raise TooHot(f"core at {temp:.0f} C")
    return stats, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=48, help="windows sampled from test_core")
    ap.add_argument("--threads", default="16,8,4")
    ap.add_argument("--batches", default="1,4")
    ap.add_argument("--dtype", default="float32", choices=["float32", "bfloat16"])
    ap.add_argument("--max-temp", type=float, default=85)
    ap.add_argument("--out", default=f"{C.RESULTS}/cpu_speed_qwen35_0.8b.json")
    a = ap.parse_args()
    cfg = json.load(open(f"{C.ROOT}/models/qwen35_0.8b_lora_saved/type1.json"))

    # test_core exactly as splits() builds it, without loading the train/val testbeds
    test = [r for r in map(json.loads, open(C.DATA)) if r["testbed"] in C.TEST]
    core = C._cap(test, C.group_key, 150, 7)
    gpu_p = np.load(f"{C.RESULTS}/{cfg['name']}.scores.npz")["test_core"]
    assert len(gpu_p) == len(core), (len(gpu_p), len(core))
    idx = sorted(random.Random(0).sample(range(len(core)), a.n))
    rows = [core[i] for i in idx]

    t = time.perf_counter()
    model, tok = load(cfg, getattr(torch, a.dtype))
    load_s = time.perf_counter() - t
    core_lens = [len(tok(r["text"])["input_ids"]) for r in random.Random(1).sample(core, 1000)]
    torch.set_num_threads(max(int(x) for x in a.threads.split(",")))
    run(model, tok, rows[:2], 1, cfg["max_len"], a.max_temp, [0.0])  # warm-up

    res, logits, peak = [], None, [hottest_core()]
    try:
        for th in map(int, a.threads.split(",")):
            torch.set_num_threads(th)
            for bs in map(int, a.batches.split(",")):
                if bs > 1 and th != max(int(x) for x in a.threads.split(",")):
                    continue
                stats, lg = run(model, tok, rows, bs, cfg["max_len"], a.max_temp, peak)
                logits = lg if logits is None else logits
                secs = sum(s for _, _, s in stats)
                toks = sum(k for _, k, _ in stats)
                per = [1000 * s / n for n, _, s in stats]
                r = {"threads": th, "batch": bs, "windows": len(rows), "secs": round(secs, 1),
                     "windows_per_s": round(len(rows) / secs, 3), "tokens_per_s": round(toks / secs, 1),
                     "ms_per_window_p50": round(float(np.median(per)), 1),
                     "ms_per_window_max": round(float(np.max(per)), 1), "peak_core_c": peak[0]}
                print(json.dumps(r), flush=True)
                res.append(r)
    except TooHot as e:
        print(f"ABORTED: {e}", flush=True)

    p = 1 - torch.softmax(torch.tensor(logits) / cfg["temperature"], -1)[:, 0].numpy() if logits is not None else None
    thr = cfg["val_threshold_fpr0.01"]
    check = None if p is None else {
        "max_abs_diff_vs_gpu": round(float(np.max(np.abs(p - gpu_p[idx]))), 4),
        "flag_agreement_vs_gpu": float(np.mean((p >= thr) == (gpu_p[idx] >= thr)))}
    tl = [len(tok(r["text"])["input_ids"]) for r in rows]
    out = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": cfg["name"], "dtype": a.dtype,
           "cpus": sorted(os.sched_getaffinity(0)), "load_seconds": round(load_s, 1),
           "sample_tokens_p50_p95": [int(np.percentile(tl, 50)), int(np.percentile(tl, 95))],
           "core_tokens_p50_p95": [int(np.percentile(core_lens, 50)), int(np.percentile(core_lens, 95))],
           "runs": res, "check": check}
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "runs"}), flush=True)


if __name__ == "__main__":
    main()
