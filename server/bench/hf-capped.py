#!/usr/bin/env python3
"""hf-capped: download a Hugging Face repository at one revision, one file at a time, with curl at a capped rate.
Resumes partial files. Why (2026-10-17): the AI box shares the home internet, and `hf download` runs several
workers uncapped; two of them at once took the house offline.

usage: hf-capped.py <repo> <revision> <dest dir> [MB/s, default 25]
"""
import json, os, subprocess, sys, urllib.request

repo, rev, dest = sys.argv[1:4]
rate = sys.argv[4] if len(sys.argv) > 4 else "25"
files = json.load(urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}/tree/{rev}?recursive=true"))
for f in (x for x in files if x["type"] == "file"):
    out = os.path.join(dest, f["path"])
    if os.path.exists(out) and os.path.getsize(out) == f.get("size"):
        continue
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    print(f"{f['path']} {f.get('size', 0) / 1e9:.2f} GB", flush=True)
    subprocess.run(["curl", "-sfL", "-C", "-", "--limit-rate", f"{rate}M", "--retry", "5", "-o", out,
                    f"https://huggingface.co/{repo}/resolve/{rev}/{f['path']}"], check=True)
print("DONE", repo, flush=True)
