"""Build labeled log windows from AIT Log Data Set V2.0 for the type-1 bake-off.

Per testbed: parse every labeled log type on every host, attach the ground-truth labels (keyed by file and line) to
events, label Suricata events from the attack itself (attacker addresses and the exfiltration domain, taken from the
labeled lines), cut each host's stream per log into windows (type1canon.canon.windows), and keep every malicious
window plus a seeded sample of benign ones.

Usage: build_ait.py OUT.jsonl [testbed ...]
"""
import bisect
import collections
import glob
import gzip
import hashlib
import ipaddress
import json
import os
import random
import re
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from type1canon import canon, parsers  # noqa: E402

ROOT = "/opt/llm/type1/data/ait"
TESTBEDS = ["russellmitchell", "santos", "fox", "harrison", "wardbeck", "shaw", "wheeler", "wilson"]
BENIGN_CAP = 6000          # benign windows kept per (testbed, source)
SEED = 13

# AIT attack-step labels -> ATT&CK tactic, most specific first (a window takes its events' most common tactic)
TACTIC_RULES = [
    ("webshell_upload", "Initial Access"),
    ("attacker_vpn", "Initial Access"),
    ("webshell_cmd", "Execution"),
    ("dnsteal", "Exfiltration"),
    ("crack_passwords", "Credential Access"),
    ("attacker_change_user", "Privilege Escalation"),
    ("escalated_command", "Privilege Escalation"),
    ("escalate", "Privilege Escalation"),
    ("network_scan", "Discovery"),
    ("service_scan", "Discovery"),
    ("dns_scan", "Discovery"),
    ("traceroute", "Discovery"),
    ("dirb", "Discovery"),
    ("wpscan", "Discovery"),
    ("attacker_http", "Discovery"),
]
# attack families for the leave-one-attack-out test
FAMILY_RULES = [("dnsteal", "exfil"), ("webshell", "webshell"), ("escalat", "escalation"),
                ("crack_passwords", "escalation"), ("attacker_change_user", "escalation"),
                ("attacker_vpn", "vpn"), ("scan", "scan"), ("dirb", "scan"), ("wpscan", "scan"),
                ("traceroute", "scan")]


def tactic_of(labels):
    for key, tactic in TACTIC_RULES:
        if key in labels:
            return tactic
    return "Discovery"


def family_of(labels):
    for key, fam in FAMILY_RULES:
        if any(key in lab for lab in labels):
            return fam
    return "other"


# log group (path relative to a host's logs/ dir, rotation suffix stripped) -> (source, parser)
def classify(rel):
    if rel == "auth.log":
        return "auth", lambda L, y: parsers.parse_syslog(L, "auth", y)
    if rel == "audit/audit.log":
        return "audit", lambda L, y: parsers.parse_audit(L)
    if rel == "dnsmasq.log":
        return "dns", lambda L, y: parsers.parse_syslog(L, "dns", y)
    if rel == "openvpn.log":
        return "vpn", lambda L, y: parsers.parse_openvpn(L)
    if rel == "suricata/eve.json":
        return "ids", lambda L, y: parsers.parse_eve(L)
    if rel.startswith("apache2/") and "error" in rel:
        return "web_error", lambda L, y: parsers.parse_web_error(L)
    if rel.startswith("apache2/"):
        return "web_access", lambda L, y: parsers.parse_web_access(L)
    return None, None


def rotation_key(path):
    m = re.search(r"\.(\d+)(?:\.gz)?$", path)
    return -int(m.group(1)) if m else 0          # .4 is oldest, the bare name is newest


def read_lines(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", errors="replace") as f:
        return f.read().splitlines()


def read_labels(path):
    out = {}
    if os.path.exists(path):
        for line in open(path):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            out[d["line"]] = d["labels"]
    return out


def internet_nets(base):
    """The testbed's simulated internet. AIT builds it from private ranges, so without this the model would learn
    "private address upstream = attacker" (the attacker, the exfiltration DNS server and remote users all sit there),
    which is backwards on a real network. Union of the firewall's internet network and the /24 of every host in
    the `internet` group, minus the VPN pool (tunnel addresses are internal)."""
    facts = {}
    for f in glob.glob(os.path.join(base, "gather/*/facts.json")):
        facts[f.split("/gather/")[1].split("/")[0]] = json.load(open(f))
    fw = facts["inet-firewall"]
    cidr = fw.get("firewall_internet_cidr", "")
    if "/" not in cidr or "UNDEFINED" in cidr:
        d = fw["ansible_default_ipv4"]
        cidr = f"{d['network']}/{d['netmask']}"
    nets = {ipaddress.ip_network(cidr, strict=False)}
    pool = [ipaddress.ip_network(f"{ip}/24", strict=False)
            for ip in facts.get("vpn", {}).get("ansible_all_ipv4_addresses", []) if ip.startswith("10.9.")]
    for h in facts.values():
        if "internet" in h.get("group_names", []):
            for ip in h.get("ansible_all_ipv4_addresses", []):
                a = ipaddress.ip_address(ip)
                if not any(a in p for p in pool):
                    nets.add(ipaddress.ip_network(f"{ip}/24", strict=False))
    return sorted(nets, key=str)


def public_remapper(tb, nets):
    """Deterministic private-sim-internet -> public address mapping for one testbed."""
    def pub(ip):
        h = hashlib.sha256(f"{tb}/{ip}".encode()).digest()
        return f"185.{h[0]}.{h[1]}.{h[2] or 1}"

    def remap(text):
        def one(m):
            try:
                a = ipaddress.ip_address(m.group(0))
            except ValueError:
                return m.group(0)
            return pub(m.group(0)) if any(a in n for n in nets) else m.group(0)
        return _IP.sub(one, text)
    return remap


def load_testbed(tb):
    base = os.path.join(ROOT, tb)
    year = int(re.search(r"start: '(\d{4})", open(os.path.join(base, "dataset.yaml")).read()).group(1))
    remap = public_remapper(tb, internet_nets(base))
    vhosts = {os.path.basename(p).split("-access")[0].split("-error")[0]
              for p in glob.glob(os.path.join(base, "gather/*/logs/apache2/*.*.*-*.log*"))}
    org = sorted({".".join(v.split(".")[-2:]) for v in vhosts if v.count(".") >= 2})
    streams = collections.defaultdict(list)          # (host, group) -> events
    for path in glob.glob(os.path.join(base, "gather/*/logs/**/*"), recursive=True):
        if not os.path.isfile(path):
            continue
        host = path.split("/gather/")[1].split("/")[0]
        rel = path.split("/logs/", 1)[1]
        group = re.sub(r"(\.\d+)?(\.gz)?$", "", rel)
        source, parse = classify(group)
        if not source:
            continue
        labels = read_labels(path.replace("/gather/", "/labels/"))
        for lines, ev in parse(read_lines(path), year):
            ev["host"] = host
            ev["msg"] = remap(ev["msg"])
            ev["labels"] = sorted({lab for n in lines for lab in labels.get(n, [])})
            ev["rot"] = rotation_key(path)
            streams[(host, group)].append(ev)
    label_ids(streams)
    return org, streams


_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def label_ids(streams):
    """Suricata has no ground truth in AIT-LDS: label its events from the attack's own traces. Events to or from an
    address seen in labeled attacker web or VPN lines, within the attack's time span, take the tactic of the nearest
    labeled event; DNS records for the exfiltration domain are Exfiltration."""
    attacker_ips, exfil, marks = set(), set(), []
    for (host, group), evs in streams.items():
        for e in evs:
            if not e["labels"] or e["source"] == "ids":
                continue
            if e["source"] == "vpn" or (e["source"] == "web_access" and "attacker_http" in e["labels"]):
                m = _IP.search(e["msg"])     # VPN: the client's address; web: the requesting client
                if m:
                    attacker_ips.add(m.group(0))
            if "dnsteal" in e["labels"] and e["source"] == "dns":
                m = re.search(r"query\[\w+\] (\S+)", e["msg"])
                if m:
                    exfil.add(".".join(m.group(1).split(".")[-2:]))
            if "dnsteal" not in e["labels"]:
                marks.append((e["ts"], e["labels"]))
    marks.sort()
    times = [t for t, _ in marks]
    lo, hi = (times[0] - 600, times[-1] + 600) if times else (0, 0)
    for (host, group), evs in streams.items():
        if group != "suricata/eve.json":
            continue
        for e in evs:
            name = re.search(r"^dns \w+ \w* ?(\S+)", e["msg"])
            if name and any(name.group(1).endswith(s) for s in exfil):
                e["labels"] = ["dnsteal", "ids_derived"]
            elif lo <= e["ts"] <= hi and any(ip in attacker_ips for ip in _IP.findall(e["msg"])):
                i = min(bisect.bisect_left(times, e["ts"]), len(times) - 1)
                near = min((j for j in (i - 1, i) if j >= 0), key=lambda j: abs(times[j] - e["ts"]))
                e["labels"] = sorted(set(marks[near][1]) | {"ids_derived"})


def build(tb):
    org, streams = load_testbed(tb)
    rng = random.Random(f"{SEED}-{tb}")
    kept, benign_seen = [], collections.Counter()
    benign_res = collections.defaultdict(list)
    for (host, group), evs in sorted(streams.items()):
        evs.sort(key=lambda e: (e["ts"], e["rot"]))
        for k, w in enumerate(canon.windows(evs)):
            labs = sorted({lab for e in w for lab in e["labels"]})
            mal = [e for e in w if e["labels"]]
            row = {"id": f"{tb}/{host}/{group}/{k}", "testbed": tb, "host": host, "source": w[0]["source"],
                   "t0": w[0]["ts"], "n": len(w), "n_mal": len(mal), "malicious": bool(mal), "labels": labs}
            if mal:
                row["tactic"] = collections.Counter(tactic_of(e["labels"]) for e in mal).most_common(1)[0][0]
                row["family"] = family_of(labs)
                row["text"] = canon.render_window(w, org)
                kept.append(row)
                continue
            row["tactic"], row["family"] = "Benign", "benign"
            src = row["source"]
            benign_seen[src] += 1
            res = benign_res[src]
            if len(res) < BENIGN_CAP:        # reservoir sample; render only what is kept
                res.append((row, w))
            else:
                j = rng.randrange(benign_seen[src])
                if j < BENIGN_CAP:
                    res[j] = (row, w)
    for src, res in benign_res.items():
        for row, w in res:
            row["text"] = canon.render_window(w, org)
            row["weight"] = benign_seen[src] / len(res)     # benign windows this one stands for
            kept.append(row)
    stats = collections.Counter((r["source"], r["malicious"]) for r in kept)
    return tb, kept, dict(benign_seen), {f"{s}/{'mal' if m else 'ben'}": n for (s, m), n in sorted(stats.items())}


if __name__ == "__main__":
    out, tbs = sys.argv[1], sys.argv[2:] or TESTBEDS
    with ProcessPoolExecutor(min(4, len(tbs))) as ex, open(out, "w") as f:   # ~2-3 GB RAM per testbed
        for tb, rows, seen, stats in ex.map(build, tbs):
            for r in rows:
                f.write(json.dumps(r) + "\n")
            print(tb, "benign windows seen:", seen, flush=True)
            print(tb, "kept:", stats, flush=True)
