"""Fetch the parts of AIT Log Data Set V2.0 (Zenodo 5789064, CC BY-NC-SA 4.0) that the type-1 dataset uses.

Range reads from the remote zips, restricted to the log types that have ground-truth labels (auth, audit, apache,
dnsmasq, openvpn), Suricata eve.json (labeled later from the attack timeline), and the label files. Zenodo answers
bursts with HTTP 429, so members are fetched two testbeds at a time with backoff; a failed member is reported.
Usage: fetch_ait.py [testbed ...]
"""
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from remotezip import RemoteZip

TESTBEDS = ["russellmitchell", "santos", "fox", "harrison", "wardbeck", "shaw", "wheeler", "wilson"]
URL = "https://zenodo.org/records/5789064/files/{}.zip?download=1"
OUT = "/opt/llm/type1/data/ait"
KEEP = re.compile(r"/logs/(auth\.log|audit/audit\.log|apache2/[^/]+\.log|dnsmasq\.log|openvpn\.log|suricata/eve\.json)"
                  r"(\.\d+)?(\.gz)?$")


def want(name):
    path = "/" + name
    if name.endswith("/") or "/gather/attacker" in path:
        return False
    if name.endswith("dataset.yaml") or "/labels/" in path:
        return True
    return "/gather/" in path and bool(KEEP.search(path))


def extract(z, info, dest, tb):
    for attempt in range(8):
        try:
            z.extract(info, dest)
            return True
        except Exception as e:  # 429 and network hiccups: back off, then retry the member
            wait = 30 * (attempt + 1)
            print(f"{tb} retry {attempt + 1} in {wait}s {info.filename}: {str(e)[:60]}", flush=True)
            time.sleep(wait)
    print(f"{tb} FAILED {info.filename}", flush=True)
    return False


def fetch(tb):
    t0, n, b, failed = time.time(), 0, 0, 0
    dest = os.path.join(OUT, tb)
    with RemoteZip(URL.format(tb)) as z:
        members = [i for i in z.infolist() if want(i.filename)]
        print(f"{tb}: {len(members)} members, {sum(i.file_size for i in members) / 1e9:.2f} GB", flush=True)
        for info in members:
            dst = os.path.join(dest, info.filename)
            if os.path.exists(dst) and os.path.getsize(dst) == info.file_size:
                continue
            if extract(z, info, dest, tb):
                n, b = n + 1, b + info.file_size
            else:
                failed += 1
            time.sleep(0.5)
    print(f"{tb}: {n} files {b / 1e9:.2f} GB in {time.time() - t0:.0f}s, {failed} failed", flush=True)


if __name__ == "__main__":
    with ThreadPoolExecutor(2) as ex:
        list(ex.map(fetch, sys.argv[1:] or TESTBEDS))
    print("ALL DONE", flush=True)
