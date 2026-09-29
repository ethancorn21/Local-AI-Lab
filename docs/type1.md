# Type-1 log triage model

A small, fast model that reads every window of the homelab's logs and answers two questions: *is this attacker
activity?* (a probability) and *which MITRE ATT&CK tactic?* (a choice). "Type 1" means System 1 in the
dual-process sense: one forward pass per decision, no text generation, calibrated probabilities, milliseconds per
window. Windows it flags or is unsure about go to a "type-2" model (the lab's Qwen3.8-27B) for a slower, reasoned
look. Target hardware: the RTX 5060 Ti 16 GB, dedicated to this job.

Started 2026-09-29. The earlier attempt on Windows event logs (the `openjev-lab` folder on the AI box, using the Laya
decision model) found that a fine-tuned Laya only tied a TF-IDF baseline and that label noise and dataset shortcuts
were the real bottleneck. This round starts from those lessons.

## Parts

| Part | Where | Who |
|---|---|---|
| Render contract: event format, anonymization, windows, window text | [type1/type1canon/](../type1/type1canon/) | Claude |
| Dataset: AIT-LDS v2.0 fetch and window builder | [type1/scripts/](../type1/scripts/) | Claude |
| Bake-off: splits, metrics, one script per method family, GPU queue | [type1/bench/](../type1/bench/) | Claude |
| Scoring server (`POST /score`) | [type1/serve/](../type1/serve/) | Claude |
| Runtime service: log inputs, streaming windower, scorer client, shadow log and flags (`type1-watch`) | harness VM project `type1-triage`, from [type1/runtime-goal/GOAL.md](../type1/runtime-goal/GOAL.md) | the local agent |

The render contract is the one piece both sides share: the model is trained on text produced by `type1canon`, and
the live service must produce byte-identical text from the same events. The agent's project vendors it read-only
(checksummed) and has golden fixtures: real raw log samples and the windows `type1canon` makes from them.

## Windows

- An **event** is `{ts, host, source, msg}`; `source` is one of auth, audit, syslog, journal, web_access, web_error,
  dns, vpn, ids (Suricata), firewall (OPNsense filterlog).
- A **window** is up to 16 consecutive events of one host and one source within 60 s.
- The **window text** has a header (`source=auth events=8 span=26s`) and one line per event with its time relative
  to the first event. Addresses become `<priv_ip_N>` / `<pub_ip_N>` (numbered by first appearance in the window, so
  "the same address again" survives but no particular address can be learned); the monitored organisation's own
  domain becomes `<org>`; lines are cut at 240 characters.

## Data: AIT Log Data Set V2.0

Eight simulated small companies ("testbeds": mail, file share, WordPress intranet, VPN, firewall, employees) with
several days of normal user activity and one scripted multi-step attack each, labeled line by line
([Zenodo 5789064](https://zenodo.org/records/5789064), CC BY-NC-SA 4.0). Linux host logs and network logs, which is
what the homelab has.

- **Attack chain** (the same in every testbed, with different parameters): VPN login with stolen credentials, network
  and service scans, dirb and WPScan against the intranet, webshell upload through a WordPress plugin exploit,
  commands through the webshell, password cracking, `su` to a real user and `sudo` to root, DNS exfiltration
  (dnsteal).
- **Labels** exist for Apache access/error, auth.log, audit.log, dnsmasq and OpenVPN. Suricata `eve.json` has none,
  so its events are labeled from the attack itself: records to or from the attacker's addresses during the attack
  (taking the tactic of the nearest labeled event) and DNS records for the exfiltration domain (flagged
  `ids_derived`).
- **Fetch:** only the logs, labels and host facts, by HTTP range reads from the Zenodo zips (the full zips are
  mostly packet captures, 131 GB). Zenodo answers bursts of range requests with HTTP 429, so the fetch uses a
  whitelist, two testbeds at a time, with backoff.
- **Splits, fixed before any result:** train = russellmitchell, fox, wardbeck, shaw, wheeler; validation = harrison;
  test = santos, wilson. Whole companies are held out, so a method cannot win by memorising one environment.
- **Sampling:** every malicious window is kept; benign windows are reservoir-sampled (6,000 per testbed and source)
  and carry a weight for the windows they stand for, so metrics use realistic prevalence. Training and validation
  take at most 300 windows per (testbed, source, attack family): 18,052 training windows. Every method is scored on
  the same stratified 4,241-window test sample (`test_core`); the fast ones also on all 127,181 test windows.

### Limits of this dataset

- **Host attacks are almost absent:** the su/sudo escalation leaves 1-3 malicious auth/audit windows per testbed.
  AIT can show whether a model catches web scans, webshells and DNS exfiltration, not Linux host attacks. Those need
  another source (a lab VM running Atomic Red Team tests with auditd is the candidate).
- **One attack chain:** held-out testbeds test a new environment, not a new attack. The leave-one-attack-out runs
  (train without one attack family, test on it) measure that instead.
- **VPN with stolen credentials** is indistinguishable from a normal login in a single window; near-zero recall
  there is expected from any type-1 model.

### Shortcut found and removed (data v2)

AIT builds its "internet" out of private address ranges. The attacker, the exfiltration DNS server and remote users
all sit there, while real internet traffic (package mirrors, public DNS) uses public addresses. On the first build
the TF-IDF baseline's strongest malicious features were `to <priv_ip_2>` and its strongest benign features
`to <pub_ip_1>`: "private upstream = attack", which is backwards on a real network. Data v2 maps each testbed's
simulated internet (the firewall's internet network, plus the /24 of every host in the `internet` group, minus the
VPN pool) to public addresses before anonymization. The baseline's area under the curve barely moved (0.992 ->
0.990), but thresholds chosen on the validation company now carry over: at the validation threshold, recall on
exfiltration went from 0.33 to 0.77 and on scans from 0.67 to 0.89.

## Metrics

Weighted by the windows each sampled window stands for. AUROC and average precision (threshold-free); recall at a
1% and 0.1% false-positive rate (what an analyst sees: the alert volume is set by the FPR); the threshold for 1% FPR
picked on validation and applied to test (does the operating point transfer to a new company?), with recall per
attack family and false-positive rate per log source at that threshold; tactic accuracy on malicious windows;
calibration error (ECE); latency batched and at batch size 1; peak VRAM.

## Results (round 1, 2026-09-29, RTX 3090 Ti at 350 W)

Test: 4,241 windows from the two held-out companies (`test_core`), data v2. "val-thr" = the threshold that gave 1%
false positives on the validation company, applied unchanged to the test companies (the realistic case: you tune on
one network and run on another).

| Method | AUROC | AP | Recall @ 1% FPR | Recall @ 0.1% FPR | val-thr recall / FPR | Recall per family @ val-thr | Tactic acc. | ECE | ms/window (batched / single) |
|---|---|---|---|---|---|---|---|---|---|
| **Qwen3.5-0.8B + LoRA (1 epoch)** | **0.997** | **0.991** | **0.972** | **0.852** | 0.985 / 2.0% | exfil 0.99, scan 0.90, webshell 0.59, escalation 0.03, vpn 0.01 | 0.743 | 0.118 | 34 / 41 |
| TF-IDF + logistic regression (CPU) | 0.990 | 0.972 | 0.850 | 0.800 | 0.863 / 1.1% | exfil 0.77, scan 0.89, webshell 0.58, escalation 0.03, vpn 0.01 | 0.747 | 0.133 | 0.7 (CPU) |
| SecureBERT 2.0 base, fine-tuned | 0.982 | 0.950 | 0.814 | 0.773 | 0.802 / 0.3% | exfil 0.52, scan 0.78, webshell 0.53, escalation 0, vpn 0 | 0.725 | 0.223 | 9 / 20 |
| Qwen3.5-2B zero-shot (answer logits) | 0.571 | 0.270 | 0.021 | 0.002 | | | 0.468 | 0.192 | 131 |
| Qwen3.5-0.8B zero-shot | 0.366 | 0.156 | 0.019 | 0.000 | | | 0.022 | 0.114 | 74 |
| Laya zero-shot, English (data v1) | 0.848 | 0.531 | 0.000 | 0.000 | | | 0.178 | 0.094 | 19 / 30 |
| Laya zero-shot, multilingual 1024 (data v1) | 0.816 | 0.433 | 0.000 | 0.000 | | | 0.083 | 0.585 | 16 / 17 |

Leave-one-attack-out, TF-IDF (trained without one attack family, recall on it at the validation threshold): DNS
exfiltration 0.00 (0.77 when seen), scans 0.22 (0.89), webshell 0.56 (0.58; its IDS windows look like the scans).

What this says so far:

1. **Zero-shot does not work.** Neither the Laya checkpoints nor small Qwen3.5 models understand log windows out of
   the box: the 0.8B model answers "no" with 86% to an obvious dirb scan. The 27B type-2 model does (reference run
   below), which is the point of having a type 2.
2. **Fine-tuning a small decoder beats the baseline where it matters.** At 1% false positives the LoRA-tuned 0.8B
   model finds 97% of attack windows against 85% for TF-IDF, and it catches almost all DNS-exfiltration windows at
   the transferred threshold. A fine-tuned encoder (SecureBERT 2.0, a ModernBERT-base model pretrained on security
   text) did *not* beat TF-IDF, the same outcome as the Windows round with Laya. Training took 88 minutes (18,052
   windows, 1 epoch) and peaked at 4.5 GB of VRAM; inference fits the 5060 Ti with room to spare, expected roughly
   twice the 3090 Ti's 41 ms per window, far more than the homelab's log volume needs.
3. **Thresholds do not transfer exactly between networks:** the 1% threshold from the validation company gave 2% on
   the test companies for the winner. On the homelab, the threshold should be set from its own shadow-mode data.
4. **Unsolved by any method: single-window escalation and VPN logins with stolen credentials.** The first has 1-3
   examples per company; the second is indistinguishable from a normal login in one window (it needs context such
   as "this account never logs in at 3 am", which is type-2 or detection-rule territory).

Not run yet (next GPU window, or on the 5060 Ti once installed): the winner again with its weights saved and with
leave-one-attack-out (does the language model generalise to an unseen attack family better than TF-IDF's 0.00?),
Qwen3.5-2B + LoRA, ModernBERT-large and Laya fine-tuned, Qwen3-Embedding + linear head, 4B/9B zero-shot. The vLLM
server was down for four hours for this round; runs were ordered by how much they could change the decision.
