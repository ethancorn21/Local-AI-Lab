"""Canonical log events, anonymization and windows for the type-1 triage model.

This module is the contract between everything that produces model input: the offline dataset builders and the
live triage service must render a given stream of events into byte-identical window texts, or the model is scored
on text it never saw in training. Change the rendering here, bump RENDER_VERSION, and rebuild the datasets.

Event: a dict with
    ts      float, seconds since the epoch (UTC)
    host    str, the machine that wrote the log (never shown to the model)
    source  str, one of SOURCES
    msg     str, the event without its syslog header (no timestamp, no hostname); raw addresses, anonymized at render
"""
import ipaddress
import re

RENDER_VERSION = 1

SOURCES = {
    "auth": "authentication and privilege use (sshd, sudo, su, cron sessions, PAM)",
    "audit": "Linux audit records (processes, logins, credential changes)",
    "syslog": "general system and service messages",
    "journal": "systemd journal entries",
    "web_access": "web server access log",
    "web_error": "web server error log",
    "dns": "DNS resolver log",
    "vpn": "VPN server log",
    "ids": "network IDS events (Suricata: alerts, DNS, HTTP, TLS, flows)",
    "firewall": "packet filter log (OPNsense / pf filterlog)",
}

MAX_EVENTS = 16          # events per window
MAX_SPAN = 60.0          # seconds from the first to the last event of a window
MAX_LINE = 240           # characters kept per rendered event

_IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.]*\d)")
_IPV6 = re.compile(r"(?<![\w:])((?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{0,4}|::1|::)(?![\w:])")
_PID = re.compile(r"^([\w./-]+)\[\d+\]:")


def _ip_class(ip):
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "linklocal_ip"
    if ip.is_multicast:
        return "multicast_ip"
    if ip.is_private or ip.is_reserved or ip.is_unspecified:
        return "priv_ip"
    return "pub_ip"


class Anonymizer:
    """Replaces addresses and the organisation's own domains with placeholder tokens.

    IPs become <priv_ip_N> / <pub_ip_N> (plus loopback, link-local, multicast), numbered by first appearance within
    one window, so the model sees "the same address again" without learning any particular address. Domains in
    `org_domains` (the monitored environment's own DNS names) become <org>; other names stay, because what an
    external name looks like is often the evidence (DNS tunnelling, typosquats).
    """

    def __init__(self, org_domains=()):
        self.ids = {}
        doms = sorted({d.lower().strip(".") for d in org_domains if d}, key=len, reverse=True)
        self.org = re.compile(r"(?i)\b(?:" + "|".join(re.escape(d) for d in doms) + r")\b") if doms else None

    def _token(self, text):
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            return text
        cls = _ip_class(ip)
        if cls == "loopback":
            return "<loopback>"
        key = (cls, ip.compressed)
        if key not in self.ids:
            self.ids[key] = sum(1 for k in self.ids if k[0] == cls) + 1
        return f"<{cls}_{self.ids[key]}>"

    def __call__(self, text):
        text = _IPV4.sub(lambda m: self._token(m.group(1)), text)
        text = _IPV6.sub(lambda m: self._token(m.group(1)), text)
        if self.org:
            text = self.org.sub("<org>", text)
        return text


def render_event(event, t0, anon):
    msg = _PID.sub(r"\1:", event["msg"].strip())
    line = f"+{event['ts'] - t0:.1f}s {event['source']} {anon(msg)}"
    line = " ".join(line.split())
    return line if len(line) <= MAX_LINE else line[:MAX_LINE - 3] + "..."


def render_window(events, org_domains=()):
    """Model input text for one window of events (already sorted by time, same host and source)."""
    anon = Anonymizer(org_domains)
    t0 = events[0]["ts"]
    span = events[-1]["ts"] - t0
    head = f"source={events[0]['source']} events={len(events)} span={span:.0f}s"
    return "\n".join([head] + [render_event(e, t0, anon) for e in events])


def windows(events, max_events=MAX_EVENTS, max_span=MAX_SPAN):
    """Split a time-sorted stream of events (one host, one source) into consecutive, non-overlapping windows.

    A window closes when it holds max_events events or the next event is more than max_span seconds after the
    window's first event. The live service applies the same rule, plus a flush of the open window when no event has
    arrived for max_span seconds, which gives the same windows.
    """
    cur = []
    for e in events:
        if cur and (len(cur) >= max_events or e["ts"] - cur[0]["ts"] > max_span):
            yield cur
            cur = []
        cur.append(e)
    if cur:
        yield cur
