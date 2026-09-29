# type1-triage: the always-on log triage service

Written 2026-09-29 by the lab's architect (Claude) for the lab's owner, who asked for this work to be handed to the local agent.

## What this is for

The lab's owner wants a small, fast "type-1" model on its own GPU that constantly reads the homelab's logs and flags attacker
activity. Type 1 means a single forward pass per decision (a classifier), no text generation: it answers "is this
window of log lines attacker activity?" (a probability) and "which MITRE ATT&CK tactic?" (a choice) in milliseconds.
Windows it is unsure about, or flags, go to a big "type-2" model later. Which model becomes type 1 is being decided
in a separate experiment on the GPU box. This project builds everything around the model: getting logs in, cutting
them into windows exactly the way the model was trained, sending them to the model, and recording what it says.

The homelab's logs: Linux hosts (auth.log/syslog, auditd, systemd journal, Apache/nginx, dnsmasq, OpenVPN) and the
network (an OPNsense firewall: its packet filter log `filterlog`, Suricata `eve.json`). An Elastic SIEM will collect
them later, so Elastic is the production input; files are the input until then.

## The contract: `type1canon/` (read-only)

`type1canon/canon.py` (event format, anonymization, windowing, window text) and `type1canon/parsers.py` (raw log
formats -> events) are the code the model's training data was made with. A model scored on text that differs from
its training text by even a space is being tested on something it never saw, so:

- Build windows with `type1canon.canon.windows` rules and render them only with `type1canon.canon.render_window`.
  Never re-implement or copy-and-modify rendering or anonymization.
- Do not edit anything in `type1canon/`. `type1canon/SHA256SUMS` must keep matching. If you think it has a bug or
  needs a change, ask the human (`ask_human`) and keep working on something else.
- New log formats get their own parser module in your package, yielding the same event dicts
  (`ts`, `host`, `source`, `msg`; `source` from `canon.SOURCES`).

`fixtures/raw/` holds real samples of every format the parsers read (from the public AIT Log Data Set V2.0, a
simulated company network with a scripted attack; CC BY-NC-SA 4.0; attack lines included). `fixtures/golden/` holds
the windows `type1canon` makes from each sample. Your pipeline, reading `fixtures/raw/<file>` through its own file
input, must produce exactly `fixtures/golden/<file>.jsonl`.

## What to build

1. **Inputs** that produce canonical events:
   - Follow log files like `tail -F`: survive rotation and truncation, remember offsets across restarts, read the
     formats `type1canon.parsers` knows (syslog with both timestamp styles, auditd, Apache/nginx access and error,
     dnsmasq, OpenVPN, Suricata eve.json).
   - New parsers: OPNsense `filterlog` lines (the pf CSV format, as it arrives by syslog), `journalctl -o json`
     output, and Elastic Common Schema documents as an Elasticsearch `_search` returns them (map `@timestamp`,
     `host.name`, the dataset or log path to a `source`, and `message`). Research these formats; make your own
     realistic fixtures for them.
2. **A streaming windower** per (host, source) that gives exactly the windows `canon.windows` gives for the same
   events (max 16 events, 60 s span), plus flushing an open window after 60 s without events, and a bounded delay
   for out-of-order events.
3. **A scorer client** for the model server's API (below): batches, timeouts, retries, and refusing to run when the
   server's `render_version` differs from `canon.RENDER_VERSION`. A mock scorer server for tests.
4. **Outputs:** shadow-mode JSONL of every scored window (window id, host, source, t0, event count, p_malicious,
   tactic and its probabilities, a hash of the text, render version) and a flags file for windows above a
   configurable threshold (per source), plus a daily summary (windows per source, flags per tactic, top flags).
5. **A CLI** `type1-watch --config <file>` that runs it all, a systemd unit example, and a README that explains setup
   and every config option.

Model server API (the type-1 server will implement this; you only call it and mock it):

```
POST /score   {"windows": [{"id": "...", "text": "..."}]}
-> 200 {"model": "...", "render_version": 1,
        "results": [{"id": "...", "p_malicious": 0.97, "tactic": "Discovery", "tactic_probs": {"Benign": 0.01, ...}}]}
GET /health   -> 200 {"model": "...", "render_version": 1}
```

## Not in scope

Training or choosing the model, GPU code, deploying Elastic, alerting to a phone.

## Done means

- The test suite passes, including: every file in `fixtures/raw/` reproduces its `fixtures/golden/` windows through
  the file input; the streaming windower matches `canon.windows` on randomized event streams; rotation, truncation
  and restart-from-offset tests; each new parser tested on fixtures; an end-to-end test (sample logs -> mock scorer
  -> shadow log, flags and summary) with known expected flags.
- `type1canon/` is unchanged (`sha256sum -c type1canon/SHA256SUMS` passes).
- The README gets someone from a fresh checkout to a running `type1-watch` against the mock scorer.
