"""Raw log formats -> canonical events (see canon.py).

Every parser takes an iterable of raw lines and yields (line_numbers, event): line_numbers are the 1-based numbers
of the raw lines that make up the event (an audit event spans several records), so ground-truth labels keyed by
line can follow the event. Lines that cannot be parsed are skipped, never guessed.
"""
import binascii
import calendar
import json
import re
from datetime import datetime, timezone

MONTHS = {m: i for i, m in enumerate(calendar.month_abbr) if m}

# "Jan 23 06:25:06 host prog[pid]: msg" (host optional: dnsmasq's own log file has none)
_BSD = re.compile(r"^(\w{3}) +(\d{1,2}) (\d\d):(\d\d):(\d\d)(?:\.\d+)? (?:(\S+) )?(\S+?(?:\[\d+\])?: .*)$")
# "2026-09-29T17:00:00.123456+00:00 host prog[pid]: msg" (rsyslog RFC 3339, Ubuntu's default since 23.10)
_RFC3339 = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)) (\S+) (\S+?(?:\[\d+\])?: .*)$")


class _SyslogClock:
    """BSD syslog lines carry no year: start from the file's year and roll over when the month goes backwards."""

    def __init__(self, year):
        self.year, self.last_month = year, None

    def ts(self, mon, day, hh, mm, ss):
        m = MONTHS[mon]
        if self.last_month is not None and m < self.last_month - 6:
            self.year += 1
        self.last_month = m
        return calendar.timegm((self.year, m, int(day), int(hh), int(mm), int(ss)))


def _iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def parse_syslog(lines, source, year, host=None):
    """auth.log, syslog, dnsmasq.log and anything else written by (r)syslog."""
    clock = _SyslogClock(year)
    for n, line in enumerate(lines, 1):
        line = line.rstrip("\n")
        m = _RFC3339.match(line)
        if m:
            yield [n], {"ts": _iso(m.group(1)), "host": host or m.group(2), "source": source, "msg": m.group(3)}
            continue
        m = _BSD.match(line)
        if m:
            mon, day, hh, mm, ss, h, msg = m.groups()
            if mon not in MONTHS:
                continue
            yield [n], {"ts": float(clock.ts(mon, day, hh, mm, ss)), "host": host or h or "", "source": source,
                        "msg": msg}


_AUDIT = re.compile(r"^type=(\w+) msg=audit\((\d+\.\d+):(\d+)\): ?(.*)$")
_KV = re.compile(r"(\w[\w-]*)=(\"[^\"]*\"|'[^']*'|\S+)")
_AUDIT_KEEP = ["op", "acct", "exe", "comm", "cmd", "cwd", "name", "success", "res", "uid", "auid", "euid", "tty",
               "terminal", "addr", "hostname", "syscall", "key"]


_ARGV = re.compile(r"a\d+")
_HEXABLE = {"proctitle", "acct", "cmd", "name", "cwd", "comm", "exe", "data"}


def _unhex(v):
    """auditd writes a string value unquoted and hex-encoded when it contains spaces or control characters."""
    if re.fullmatch(r"(?:[0-9A-F]{2})+", v):
        try:
            return binascii.unhexlify(v).decode("utf-8", "replace").replace("\x00", " ").strip()
        except (binascii.Error, ValueError):
            pass
    return v


def _audit_fields(body, rtype):
    fields = {}
    for k, v in _KV.findall(body):
        if k == "msg" and v.startswith("'"):
            fields.update(_audit_fields(v.strip("'"), rtype))
            continue
        quoted = v[:1] in "\"'"
        v = v.strip("\"'")
        if k in ("auid", "uid", "euid") and v == "4294967295":
            v = "unset"
        # SYSCALL's a0..a3 are raw register values; only EXECVE's a0..aN are argv strings
        if not quoted and (k in _HEXABLE or (rtype == "EXECVE" and _ARGV.fullmatch(k))):
            v = _unhex(v)
        fields.setdefault(k, v)
    return fields


def parse_audit(lines, host=""):
    """auditd's audit.log: records sharing msg=audit(time:serial) form one event."""
    events = {}
    for n, line in enumerate(lines, 1):
        m = _AUDIT.match(line.rstrip("\n"))
        if not m:
            continue
        rtype, ts, serial, body = m.groups()
        ev = events.setdefault((ts, serial), {"lines": [], "types": [], "fields": {}, "argv": None})
        ev["lines"].append(n)
        ev["types"].append(rtype)
        f = _audit_fields(body, rtype)
        if rtype == "EXECVE":
            argc = int(f.get("argc", 0) or 0)
            ev["argv"] = " ".join(f.get(f"a{i}", "") for i in range(argc))
        elif rtype == "PROCTITLE" and ev["argv"] is None:
            ev["argv"] = f.get("proctitle")
        for k, v in f.items():
            ev["fields"].setdefault(k, v)
    for (ts, _), ev in sorted(events.items(), key=lambda x: (float(x[0][0]), int(x[0][1]))):
        f = ev["fields"]
        parts = ["+".join(dict.fromkeys(ev["types"]))]
        parts += [f"{k}={f[k]}" for k in _AUDIT_KEEP if k in f and f[k] not in ("?", "(null)", "")]
        if ev["argv"]:
            parts.append(f'argv="{ev["argv"]}"')
        yield ev["lines"], {"ts": float(ts), "host": host, "source": "audit", "msg": " ".join(parts)}


# optional "vhost:port " prefix (other_vhosts_access.log), then the combined log format
_ACCESS = re.compile(r'^(?:(\S+:\d+) )?(\S+) \S+ (\S+) \[([^\]]+)\] (".*)$')


def parse_web_access(lines, host=""):
    for n, line in enumerate(lines, 1):
        m = _ACCESS.match(line.rstrip("\n"))
        if not m:
            continue
        vhost, client, user, ts, rest = m.groups()
        try:
            t = datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z").timestamp()
        except ValueError:
            continue
        msg = f"{client} " + (f"user={user} " if user != "-" else "") + rest
        if vhost:
            msg = f"vhost={vhost.rsplit(':', 1)[0]} " + msg
        yield [n], {"ts": t, "host": host, "source": "web_access", "msg": msg}


_ERROR = re.compile(r"^\[(\w{3} \w{3} \d{1,2} \d\d:\d\d:\d\d(?:\.\d+)? \d{4})\] (.*)$")


def parse_web_error(lines, host=""):
    """Apache error log. Continuation lines without a timestamp (child processes writing to stderr) inherit the
    previous line's time."""
    last = None
    for n, line in enumerate(lines, 1):
        line = line.rstrip("\n")
        m = _ERROR.match(line)
        if m:
            ts, msg = m.groups()
            fmt = "%a %b %d %H:%M:%S.%f %Y" if "." in ts else "%a %b %d %H:%M:%S %Y"
            last = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc).timestamp()
            msg = re.sub(r"\[pid \d+(?::tid \d+)?\] ", "", msg)
        elif last is not None and line.strip():
            msg = line
        else:
            continue
        yield [n], {"ts": last, "host": host, "source": "web_error", "msg": msg}


_OVPN = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*)$")


def parse_openvpn(lines, host=""):
    for n, line in enumerate(lines, 1):
        m = _OVPN.match(line.rstrip("\n"))
        if m:
            t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            yield [n], {"ts": t, "host": host, "source": "vpn", "msg": m.group(2)}


def _endpoints(d):
    def ep(ip, port):
        return f"{ip}:{port}" if port is not None else f"{ip}"
    return f"{d.get('proto', '')} {ep(d.get('src_ip'), d.get('src_port'))} -> {ep(d.get('dest_ip'), d.get('dest_port'))}"


def render_eve(d):
    """One Suricata eve.json record -> message text, or None for record types that are not security events."""
    t = d.get("event_type")
    if t == "alert":
        a = d.get("alert", {})
        return (f'alert sid={a.get("signature_id")} "{a.get("signature", "")}" category="{a.get("category", "")}" '
                f'severity={a.get("severity")} action={a.get("action")} {_endpoints(d)}')
    if t == "dns":
        x = d.get("dns", {})
        if x.get("type") == "query":
            return f'dns query {x.get("rrtype", "")} {x.get("rrname", "")} {_endpoints(d)}'
        answers = x.get("answers") or x.get("grouped", {})
        if isinstance(answers, list):
            data = " ".join(str(a.get("rdata", "")) for a in answers[:3])
        else:
            data = " ".join(str(v) for vs in answers.values() for v in (vs if isinstance(vs, list) else [vs]))[:120]
        return f'dns answer {x.get("rrtype", "")} {x.get("rrname", "")} rcode={x.get("rcode", "")} {data} {_endpoints(d)}'
    if t == "http":
        h = d.get("http", {})
        return (f'http {h.get("http_method", "")} {h.get("hostname", "")}{h.get("url", "")} status={h.get("status", "")} '
                f'len={h.get("length", "")} ua="{h.get("http_user_agent", "")}" {_endpoints(d)}')
    if t == "tls":
        x = d.get("tls", {})
        return f'tls sni={x.get("sni", "")} version={x.get("version", "")} subject="{x.get("subject", "")}" {_endpoints(d)}'
    if t == "ssh":
        x = d.get("ssh", {})
        return (f'ssh client="{x.get("client", {}).get("software_version", "")}" '
                f'server="{x.get("server", {}).get("software_version", "")}" {_endpoints(d)}')
    if t == "flow":
        f = d.get("flow", {})
        return (f'flow {d.get("app_proto", "")} {_endpoints(d)} pkts={f.get("pkts_toserver")}/{f.get("pkts_toclient")} '
                f'bytes={f.get("bytes_toserver")}/{f.get("bytes_toclient")} age={f.get("age")} state={f.get("state", "")}')
    if t == "fileinfo":
        x = d.get("fileinfo", {})
        return f'fileinfo {x.get("filename", "")} size={x.get("size", "")} {_endpoints(d)}'
    if t == "anomaly":
        x = d.get("anomaly", {})
        return f'anomaly {x.get("type", "")} {x.get("event", "")} {_endpoints(d)}'
    return None


def parse_eve(lines, host=""):
    for n, line in enumerate(lines, 1):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = render_eve(d)
        if msg:
            yield [n], {"ts": _iso(d["timestamp"]), "host": host, "source": "ids", "msg": " ".join(msg.split())}
