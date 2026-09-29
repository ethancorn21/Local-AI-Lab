"""Cut small real-format samples out of AIT-LDS v2.0 (with their attack lines) and render golden windows for them.

The golden windows pin the runtime service to the exact text the model is trained on: the service's own file
adapters must turn fixtures/raw/* into exactly fixtures/golden/*.jsonl.
Usage: make_fixtures.py OUTDIR
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from type1canon import canon, parsers  # noqa: E402

TB = "/opt/llm/type1/data/ait/russellmitchell"
ORG = ["russellmitchell.com"]
YEAR = 2022
SAMPLES = [  # name, file under gather/, parser, lines around the first labeled line
    ("auth.log", "intranet_server/logs/auth.log", lambda L: parsers.parse_syslog(L, "auth", YEAR), 150),
    ("audit.log", "intranet_server/logs/audit/audit.log", lambda L: parsers.parse_audit(L), 200),
    ("dnsmasq.log", "inet-firewall/logs/dnsmasq.log", lambda L: parsers.parse_syslog(L, "dns", YEAR), 150),
    ("openvpn.log", "vpn/logs/openvpn.log", lambda L: parsers.parse_openvpn(L), 150),
    ("access.log", "intranet_server/logs/apache2/intranet.smith.russellmitchell.com-access.log.2",
     lambda L: parsers.parse_web_access(L), 150),
    ("error.log", "intranet_server/logs/apache2/intranet.smith.russellmitchell.com-error.log.2",
     lambda L: parsers.parse_web_error(L), 150),
    ("eve.json", "inet-firewall/logs/suricata/eve.json", lambda L: parsers.parse_eve(L), 250),
]


def first_label(path):
    try:
        return min(json.loads(line)["line"] for line in open(path))
    except (OSError, ValueError):
        return 1


def main(out):
    os.makedirs(f"{out}/raw", exist_ok=True)
    os.makedirs(f"{out}/golden", exist_ok=True)
    for name, rel, parse, half in SAMPLES:
        lines = open(f"{TB}/gather/{rel}", errors="replace").read().splitlines()
        if name == "eve.json":   # the attack starts late in eve; take the head, which has every record type
            chunk = lines[:2 * half]
        else:
            first = first_label(f"{TB}/labels/{rel}")
            chunk = lines[max(0, first - 1 - half):first - 1 + half]
        with open(f"{out}/raw/{name}", "w") as f:
            f.write("\n".join(chunk) + "\n")
        events = sorted((e for _, e in parse(chunk)), key=lambda e: e["ts"])
        with open(f"{out}/golden/{name}.jsonl", "w") as f:
            for w in canon.windows(events):
                f.write(json.dumps({"t0": w[0]["ts"], "n": len(w), "text": canon.render_window(w, ORG)}) + "\n")
        print(name, len(chunk), "lines", len(events), "events")


if __name__ == "__main__":
    main(sys.argv[1])
