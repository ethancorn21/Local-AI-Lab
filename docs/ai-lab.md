# AI Lab

The hub note for the local AI lab: the GPU server, the model it serves, the isolated VM where the coding agent works, how they are connected and secured, what was measured, and what is planned. Kept up to date after every change (see the changelog at the end). Internal addresses, VLAN numbers, account names and key material are left out on purpose.

Related notes:
- [Architecture](architecture.md): how the work is split between the cloud architect, the orchestrator script and the per-task local agents.
- [Local Coding Agent Harness - Architecture and Decisions](agent-harness.md): the agent side (loop, memory, tasks, experiments, operating commands).
- [Local Autonomous Coding Agent Build (first build, 2026-09-25)](history/first-build-2026-09-25.md): the first build session (2026-09-25), partly out of date.
- [Type-1 log triage model](type1.md): the small always-on model that flags attacker activity in the homelab's logs (for the RTX 5060 Ti): data, bake-off, results.

## Overview

| Part | Role |
|---|---|
| AI box (trusted LAN) | GPU server running the model. Serves only on localhost. |
| Harness VM (untrusted, isolated lab VLAN) | The coding agent's own machine: the agent harness, the loop driver, its projects, a search engine for its web research. |
| Link | SSH tunnels from the VM to the AI box, one per model port; the tunnel key can forward only the listed model ports (see [Ports](#ports-and-tunnels)). |
| Operator | Laptop -> VM over SSH into tmux (live agent view, loop control); laptop -> AI box for administration. |
| Claude | Architect/designer: turns the operator's intent into designs, seed tasks and harness rules; administers both machines through its own accounts. |

## Hardware (AI box)

| Item | Now | Notes |
|---|---|---|
| CPU | Intel i9-14900KF | History of instability under Windows (suspected Raptor Lake degradation); P-cores capped, power limited to 125 W (PL1 = PL2). No hardware errors seen under Linux so far. |
| GPU 0 | RTX 3090 Ti 24 GB (Ampere), slot 1 (x16) | Replaced the RTX 4080 SUPER 16 GB on 2026-09-27. Power cap 350 W. Production model (vLLM), pinned to this card by UUID. |
| GPU 1 | RTX 5060 Ti 16 GB (Blackwell), slot 3 (PCIe 4.0 x4, chipset) | Installed 2026-10-01. Stock 180 W. Role under test: a third coding model ([below](#second-card-rtx-5060-ti-2026-10-01)). |
| GPU coming | A second 24 GB card (undecided; a used RTX 3090 is the value pick) | The 5060 Ti cannot share a model with the 24 GB cards (the model's 4 KV heads allow splitting across exactly 2 or 4 identical cards). |
| RAM | 32 GB DDR5 | If upgraded: a 2-stick kit (2x32 or 2x48), not 4 sticks (two DIMMs per channel slows DDR5 and stresses the memory controller). |
| Board | MSI PRO Z790-P WIFI | Slot 1 PCIe 5.0 x16 (CPU), slot 3 PCIe 4.0 x4 (chipset), the rest PCIe 3.0 x1. |
| PSU | EVGA SuperNOVA 1300 G2 (single rail, 6 PCIe power sockets) | Plan for three cards: 3090 Ti A on 2-3 sockets, 3090 Ti B on 2, 5060 Ti plus its powered riser on 1. EVGA G2/G3/G5/P2/T2 modular cables are cross-compatible. The current card runs on 2 cables (one daisy-chained); the 350 W cap keeps it well within that. |
| Storage | 1.8 TB NVMe, LVM | Root 200 GB, a separate volume for models and apps, ~50 GB unallocated for snapshots. |
| Chassis | Case with a CPU water cooler | Rear radiator fan hub has no power (no Molex cable); an open-frame mining rig with risers is planned for the three cards. |

### Power and heat

- Power limits are applied at boot by a service that the model server and Docker depend on: nothing serves uncapped. It sets CPU PL1/PL2 to 125 W, GPU persistence mode and the 350 W GPU cap, reads them back, and fails if they did not take.
- A temperature guard records CPU package and hottest core and, for every GPU, temperature, power, fan, load and the thermal-slowdown flags every 15 s (one row per card). It sends a phone alert through the doorbell (a telecloak message) when a card stays at 83 C (the 3090 Ti's target) for 2 minutes, throttles for a minute, or runs its fan at 95% for 5 minutes (one alert per card and condition per 30 minutes). A card at 88 C for a minute gets its own model server stopped (vLLM for the 3090 Ti, the llama.cpp server for the 5060 Ti; a card without an entry only alerts), and the CPU's hottest core at 95 C for a minute stops every model server; each stop is alerted with the command that restarts it. A self-test (`hwtemps-log --selftest`) checks these decisions on synthetic readings. Until 2026-10-02 it read only the first card; that history is kept as a separate file.
- Observed (blower fans off, agent loop running): CPU hottest core 38-76 C, GPU 44-80 C at ~348 W, GPU fans up to ~89%, no thermal throttling (the card slows down at 94 C). A 300 W cap is an option if it runs warmer in the open frame.
- Airflow in the open frame (2026-10-02): the frame's fans blow through the CPU radiator first, and the 3090 Ti sits right above the 5060 Ti's exhaust. 3090 Ti medians at ~348 W and full load: 73-75 C at 83-85% fan in the case, 64 C at 77% in the open frame alone, 69 C (p95 74) at 80% with the 5060 Ti busy below it (the CPU also ran ~3 C warmer, so part of it is ambient). Safe, but with less headroom; a 3090 FE (memory chips on the back, cooled only by the backplate) will need air on its backplate.

## Operating system

- Ubuntu, headless (no desktop: frees VRAM and speeds up prompt processing on a single-GPU box).
- NVIDIA open driver from Canonical-signed module packages, so Secure Boot stays on; CUDA toolkit installed on its own (not the `cuda` meta-package, which would replace the signed driver).
- Docker plus the NVIDIA container toolkit. The AI box has no host firewall, so every container and service is bound to 127.0.0.1.

## Model serving

**Production (since 2026-09-27):** Qwen3.8-27B, 4-bit (W4A16 AutoRound), on vLLM with the HyperQwen patch set, in Docker.

| Setting | Value |
|---|---|
| Speed | ~106 tok/s short prompts, ~99 tok/s with ~52k tokens of context (thinking on) |
| Speculative decoding | MTP, 3 draft tokens, 69% accepted, ~3.1 tokens per step |
| Context | 150k per request; 200k tokens of KV cache in total (1.34 full-size requests at once) |
| Prefix caching | On: 94% of prompt tokens are served from cache in agent sessions |
| Vision | On (up to 8 images per request); the agent can look at screenshots |
| Thinking | Effort xhigh (a sentence in the chat template); per-turn thinking budget 16k (set by the harness) |
| Quality | Perplexity +1.27% versus an 8-bit reference on the lab's own code corpus |

The llama.cpp service is kept, disabled, as a fallback.

**Model:** Qwen3.8-27B is a hybrid: most layers use DeltaNet (linear attention with a fixed-size state) and only 16 use full attention, so context memory grows slowly with context length.

### Benchmarks on the 3090 Ti (2026-09-27)

llama.cpp, generation speed (tok/s) at 0 / 32k / 64k tokens of context, 350 W:

| Quant | Speed | Max all-GPU context (no MTP / MTP) | KL divergence vs 8-bit (mean, 8-bit KV) |
|---|---|---|---|
| IQ4_XS | 49 / 40 / 34 | 262k / 196k | 0.031 |
| Q5_K_M | 38 / 32 / 28 | 131k / 82k | 0.0105 |
| Q5_K_XL | 37 / 31 / 27 | 114k / 65k | 0.0105 |
| Q6_K | 34.5 / 29 / 25 | 114k | 0.0087 |

KV cache precision had almost no effect on speed. n-gram speculation was useless (thinking output never repeats). MTP on llama.cpp: 45-55% acceptance at temperature 1.0.

Engines compared on the same prompts and client:

| Engine | Speed short / long | Prefill | Quality (perplexity vs 8-bit) | Notes |
|---|---|---|---|---|
| **vLLM HyperQwen (chosen)** | **106 / 99 tok/s** | ~1,110 tok/s | +1.27% | 150k context, supports two-card splitting |
| NInfer-3090 | 65 / 55 tok/s | ~860 tok/s | about +0.8% | single GPU only; needs a pinned model revision (the latest one is unreadable) |
| llama.cpp IQ4_XS + MTP | 63-72 / 56-65 tok/s | ~1,500 tok/s | KL divergence 0.031 vs 8-bit (perplexity not measured) | the previous production setup |

**Two-card numbers (from the HyperQwen project's tests on two 3090s):** splitting one model across both cards speeds up a single request only 16-35%, because the cards sync over PCIe twice per layer (NVLink helps little). Two separate copies of the model give about twice the total work. One card serving several requests at once scales almost linearly (measured here: one agent 78 tok/s, plus a second request 192 tok/s combined), so the limit on parallel agents is context memory, not compute.

### Second card: RTX 5060 Ti (2026-10-01)

Question: what should the 16 GB card run as a third coding model? Tested on the card alone (llama.cpp, the production model on the 3090 Ti untouched):

| Model | Placement | Generation, tok/s (fresh / 32k / 64k / 96k context) | Prompt reading, tok/s (same) |
|---|---|---|---|
| Qwen3.6-35B-A3B (mixture of experts, 3B active), IQ4_XS 17.7 GB | 13 GB on the card; experts of 12 of 40 layers in system RAM | 84 / 67 / 56 / 48 | 1890 / 1556 / 1259 / 1101 |
| Qwen3.8-27B (dense, the production model), IQ4_XS 14.3 GB | All on the card (the old 4080 SUPER setup) | 28 / 23 / 20 / 17 | 966 / 715 / 569 / 472 |

- **Why the MoE is 3x faster:** generation speed is roughly memory bandwidth divided by the bytes read per token. The dense 27B reads all ~14 GB of weights for every token; the MoE reads only its ~3B active parameters. The 27B numbers are the 4080 SUPER's scaled by the bandwidth ratio (448 vs 736 GB/s), as predicted.
- **The x4 slot matters only for prompt reading, and only for the MoE.** Generation reads the card's own memory. For a batch of prompt tokens, llama.cpp copies the RAM-resident experts to the card over PCIe, so bigger batches pay that copy less often: 2048-token batches read prompts at 1890 tok/s, 512-token batches at 843. Computing those layers on the CPU instead was slower (1290).
- **Experts in RAM:** with 10 layers' experts in RAM the run crashed at 64k context (CUDA ran out of scratch memory during the run, after a clean start); 11 and 12 ran to 96k, within 3% of each other. The server uses 12 and passed a full 114,583-token prompt (41 tok/s at the edge).
- **Quality (tested 2026-10-02 with the effort A/B probes, [results](effort-ab.md#model-arm-qwen36-35b-a3b-mixture-of-experts-on-the-rtx-5060-ti-2026-1001-02)):** working code on the hidden checks, but a blind judge preferred the production 27B's code in 21 of 23 probe pairs (tests 24 of 24), the MoE noticed 6 of 16 planted spec flaws (27B: 13/16), and it repeatedly rewrote the human's acceptance criteria and ignored the driver's correction (32 rejected done claims vs 0). Same wall time per probe as the 27B on the 3090 Ti. Not a good general coding agent for this lab; Qwen's published benchmarks already put the same-generation dense 27B ahead (SWE-bench Verified 77.2 vs 73.4, Terminal-Bench 2.0 59.3 vs 51.5).
- Server: `llama-5060ti.service`, pinned to the card by UUID and to the P-cores. The MoE test configuration (148k window, experts of 13 layers in RAM) is kept as [an example](../server/llama.cpp/llama-5060ti-moe.service.example); since 2026-10-02 the unit serves the dense 27B ([config](../server/llama.cpp/llama-5060ti.service)): all on the card, 114k window, the production vLLM chat template, a 114k-token prompt tested. Not enabled at boot yet.

### Ports and tunnels

Every model server listens on localhost on the AI box and appears at the same port on the VM's localhost through its own SSH tunnel (`llm-tunnel@<port>` on the VM, [example](../harness/system/llm-tunnel@.service.example)). The AI box decides what is reachable: the tunnel key lists exactly the ports that have a live service. Adding a model = one allowlist entry on the AI box + `systemctl enable --now llm-tunnel@<port>` on the VM; no firewall change.

| Port | Service |
|---|---|
| 8080 | Production model, vLLM on the 3090 Ti |
| 8081 | Reserved: the second 24 GB card |
| 8082 | llama.cpp on the RTX 5060 Ti |
| 8090 | Reserved: the type-1 scorer |

One tunnel per port, so a broken forward cannot take the production port down with it. A model gateway (one port, routing by model name) was considered and not built: it would put another always-on component in front of every request; worth it only with many models.

### Lessons from serving

1. Measure placement, don't assume: each GB of model offloaded to system RAM cost ~14 ms per token on the old card.
2. 4-bit is the right trade for an agent: 8-bit halves the speed for about a 1% perplexity gain, and an independent agent benchmark found 8-bit no better than 4-bit on coding tasks.
3. Bigger context only helps if the workflow uses it; the agent hands over at 120k of the 150k window.
4. The model server's own metrics (speculative acceptance, prefix-cache hits, preemptions) are the best health check.

## Network and security

- **Segmentation:** the AI box is on the trusted LAN; the harness VM is in an isolated lab VLAN. The firewall allows only VM -> AI box on SSH. The model API itself is never exposed to the network.
- **The tunnel key can do one thing:** forward the listed model ports, from the VM's address only, with no shell. Other forwards are refused ("administratively prohibited"; re-verified 2026-10-01 for an unlisted port after adding the second one).
- **Separate identities** for the operator, Claude, the model service, the tunnel and the agent, so every action in the auth log is attributable and each can be revoked on its own. SSH logs record the key fingerprint used.
- **The agent's permissions (its own VM):** it may install system packages (`sudo apt-get` only, which is root-equivalent on that VM, a deliberate decision) and research the web through a local search engine and a page fetcher that labels results as untrusted. Egress lockdown was considered and decided against; the VLAN isolation is the boundary.
- **The doorbell, now an encrypted relay (telecloak):** the Telegram bot token and the telecloak key live on the AI box, not on the VM the agent controls. The VM reaches them only through an SSH key whose forced command is the relay (source address pinned, no PTY, no forwarding; verified that a PTY and port forwards are refused). The relay takes exactly three verbs (`send` a message, `fetch` the human's messages, `ack` them); anything else rings the fixed ping as before. Outgoing text is encrypted on the AI box (AES-256-GCM, a pre-shared key, one key per direction), so Telegram carries only ciphertext and the agent never touches keys. Incoming messages count only if they come from the human's own Telegram account *and* decrypt with the human-to-bot key, are at most a day old, were not seen before (replays) and are a well-formed answer, message or command (start, stop, status). Without a key only the fixed ping ever leaves, never message text. Rate-limited (20 an hour, 100 a day). The trade-off accepted: the agent now chooses the words the human reads (a prompt-injected agent could show a malicious command), so the desktop app shows plain text only and requests stay untrusted input. The desktop app is [telecloak](https://github.com/ethancorn21/telecloak).
- **Supply-chain lessons:** the Pi harness once replaced itself at runtime with a renamed package (legitimate, but an unattended host must never change its own code: self-updates are now off and versions pinned); and before it had package rights, the agent fetched unsigned Debian packages over plain HTTP to get test dependencies. That is why it now has proper apt access instead. Since 2026-10-02, Python packages apt lacks go into a per-project virtualenv from PyPI with every version and hash pinned (the driver builds it with `pip install --require-hashes` before every test run, so an altered or unpinned package fails), and model weights come only through the library's own download at a pinned revision with recorded checksums. The earlier blanket ban on downloads had kept the agents from any pretrained model: the first frontpage round shipped a word-count "embedding".

## Harness VM

- Ubuntu 24.04, 6 vCPU, 15 GB RAM, 48 GB disk.
- Services: the model tunnel (restarts itself), SearXNG (localhost only) for the agent's web search, a text-extraction venv for its page fetcher.
- The agent's tools: the Pi coding agent with the lab's extensions, the loop driver and helpers (`agent-loop`, `agent-report`, `task-audit`, `codemap-gen`, `decisions-archive`, `newproj`), Playwright and a game screenshot tool.
- Everything about the agent itself: [Local Coding Agent Harness - Architecture and Decisions](agent-harness.md).

## Projects

| Project | What | Status |
|---|---|---|
| sshreport | sshd log analyzer, first loop test | Done 2026-09-25 (3 tasks, 19 min, 43 tests) |
| valtest | Validation project for the vLLM switch | Done 2026-09-27 (2 tasks incl. vision, 3.5 min) |
| hollowdeep | Browser game: v1 Diablo-like dungeon crawler, v2 twin-stick, v3 top-down looter shooter (guns, gadgets, grid inventory, armed enemies) | v1 done 09-25, v2 done 09-26, v3 in progress (tasks 022-025 done, 026 in progress). ~7,400 lines of code, ~12,200 lines of tests (405 tests), ~300 commits after ~40 hours of agent time. |
| frontpage | Personal reading feed: Hacker News-style, curated to my interests by a local sentence-embedding model (all-MiniLM-L6-v2, pinned and hash-checked), research papers first-class, no algorithm pits (recent behaviour capped at about a third of the ranking), feedback on every item, a reading trail and a balance page; static, minimal design in espresso/latte brown. The first team-mode project (two agents). | Round 1 2026-10-02 (25 tasks, 302 tests, ~4.9 h); round 2 the same day after the human's review (pretrained embeddings, many more sources, actions on every item, working read and balance pages, ready-made interests, browser end-to-end tests): 40 tasks in all, ~6,000 lines of code, ~9,400 lines of tests. A live preview follows main ([harness/tools/frontpage-preview](../harness/tools/frontpage-preview/run.sh)). Final browser-based goal check running. |

## Plans

- **Second 24 GB GPU (the 3090 Ti order was cancelled; a used plain RTX 3090 is the value pick):** two agents on the same project, one model copy per card (measured: two agents on one card give only ~1.06x). Design in the harness note. A pair of RTX 5060 Ti 16 GB costs about the same as one 3090 but only runs the model by splitting it across both cards over this board's x16 + x4 links; it would at best tie a 3090 for one agent, so it is worth it only for a small-model tier of agents (untested).
- **RTX 5060 Ti:** installed 2026-10-01. The MoE coding-model test argued against the MoE ([above](#second-card-rtx-5060-ti-2026-10-01)); since 2026-10-02 the card runs the dense 27B as the second agent of [team mode](agent-harness.md#team-mode-two-agents-on-one-project-built-2026-10-02), until the SIEM work needs it. The type-1 log triage model ([type1.md](type1.md)) waits for that. vLLM is pinned to the 3090 Ti. The temperature logger still reads only the first card: the operator decided against extending it (the 5060 Ti relies on its own firmware throttling).
- **Open-frame rig** with risers for the three cards; re-check temperatures and power caps after the move.
- **Model options considered:** Qwen3.8-Flash-Next (a 125B mixture-of-experts model, 6B active, ~90 GB at 4-bit) was noted as a future target; it does not fit in 64 GB of VRAM, so not pursued for now.
- Do-later list for the agent side: in the harness note.

## Changelog

| Date | Change |
|---|---|
| 2026-09-24 | Project start: Windows box repurposed, plan for a local coding agent. |
| 2026-09-25 | Headless Ubuntu with signed NVIDIA driver; llama.cpp serving Qwen3.8-27B IQ4_XS (114k context, 42 tok/s on the 4080 SUPER); harness VM in the lab VLAN with the one-port tunnel; harness bake-off (Pi 84/84 vs Qwen Code 83/84), Pi chosen; first autonomous loop; hollowdeep v1 built. |
| 2026-09-26 | Per-session ledger with configuration stamps; journal archive; code-enforced hand-over; thinking effort control fixed; vision enabled; hollowdeep v2 done. |
| 2026-09-27 | GPU swapped to RTX 3090 Ti; CPU/GPU power limits at boot; temperature logging with overheat stop; quant/engine sweep; vLLM HyperQwen in production (~100 tok/s, 150k); agent web research and package installs; agile tasks (agent splits and adds tasks, human criteria fixed); generated CODEMAP; Pi's own terminal UI as the live view; hollowdeep v3 started. |
| 2026-09-28 | Hand-over raised to 120k with exact output clamping at the window edge; per-task test baselines; driver waits for the model server, short driver notes, stall flag, `agent-report`; "check facts by running code" and "noticed problems become new tasks" rules; project template fixed for new projects; documentation notes created; A/B test of hand-over notes in task files started; vision sessions fixed (image budget per request) after the A/B exposed HTTP 400 crashes past 8 screenshots; driver logs sessions that end on a server error; lab moved into the Local-AI-Lab repository. Overnight: hand-over A/B (4 runs) found task-file notes as reliable and effective as PROGRESS.md; Pi vs Oh My Pi bake-off: Oh My Pi ~70% slower, no fewer edit failures, Pi stays. Second 3090 Ti order cancelled. Two agents on one GPU measured at ~1.06x throughput (MTP saturates the GPU, 243 context preemptions in 2 h): one agent per GPU. Live loop switched to task-file hand-over notes; driver done-signal made per project (it ended both concurrent sessions). The live loop stopped at session 200: the driver's safety cap (MAX_ITERS) counted the project's total sessions, not the current run; now per run. New projects start from a folder with a `GOAL.md`: the agent plans (000), builds, and checks against the goal (999); edits to `GOAL.md` re-plan. No tmux: loops run in the background (`agent-start` / `agent-stop`); `agent-watch` redone Claude Code-style with a message line (human -> agent messages mid-session, or stop-and-send). Agent -> human requests: `ask_human` tool, content-free Telegram doorbell triggered through a forced-command SSH key to the AI box, `agent-talk` to answer (live talk or reply), blocking requests hold the loop; tested end to end. |
| 2026-09-29 | Requests block only their task, not the loop. telecloak: the doorbell became an encrypted two-way relay (agent requests reach the human's phone as ciphertext and are read in the telecloak desktop app; answers, messages and start/stop/status commands come back the same way and are carried into the projects by the `telecloak-pull` service on the VM). Key set up and tested end to end on a throwaway project: an encrypted request reached the app, the answer from the app marked it answered, messages landed in the project inbox, and status, start and stop replied (7 of 7 accepted, 1 old unencrypted message rejected). Type-1 log triage started ([type1.md](type1.md)): AIT Log Data Set V2.0 fetched (logs and labels only), windows built with a shared render contract, a simulated-internet address shortcut found and removed; round-1 bake-off on the 3090 Ti (vLLM down for four hours): Qwen3.5-0.8B with LoRA leads (AUROC 0.997, 97% recall at 1% false positives vs 85% for TF-IDF); zero-shot small models and Laya do not work on logs. The local agent builds the runtime service (`type1-triage`, from a GOAL.md). |
| 2026-09-30 | Effort A/B v2 built (spec reading with planted flaws and clean controls, blind Claude judge, mutation score, a multi-step security project through the whole loop); runs unattended once the GPU is free; type-1 round 2 stopped behind it (A/B first, by the human's call). Harness VM housekeeping (throwaway projects, stray folders, an orphaned monitor). See effort-ab.md. AGENTS.md trimmed to 78 lines: rules the harness already delivers at the point of use removed (agent-harness.md, decisions). |
| 2026-10-01 | Effort A/B v2 finished (54 runs) and judged: `medium` used about half the tokens and 55-60% of the wall time of `xhigh` (about 1.8x project throughput) with the same hidden-check correctness; side by side, a blind judge preferred `xhigh`'s small-task code in 18 of 21 pairs (tests, robustness; `medium`'s was simpler), with no overall winner on the 3 project pairs; `xhigh` noticed slightly more planted spec flaws and made fewer early done claims. Asking the human, not effort, decided whether a noticed flaw ended in the author's intended behaviour. The judge now stops on a subscription limit instead of failing through. See effort-ab.md. RTX 5060 Ti installed (x4 chipset slot); vLLM pinned to the 3090 Ti by UUID; model tunnels became one `llm-tunnel@<port>` per model port with a port plan (8082 = the 5060 Ti); on the 5060 Ti, Qwen3.6-35B-A3B (MoE) generates 84 tok/s fresh / 48 at 96k vs 28 / 17 for the dense 27B; MoE quality test via the A/B probes next. AGENTS.md: principles for code that stays easy to change and "refactor first, then add" (a behavior-preserving refactor commit before changing long or special-cased code); deployed to the VM template together with the undeployed 09-30 trim and its driver and web-tool wording changes (existing projects keep their own copies). |
| 2026-10-02 | MoE arm of the effort A/B (24 probe runs, Qwen3.6-35B-A3B on the RTX 5060 Ti, thinking on), judged blind against the 27B at `xhigh`: same hidden-check correctness and wall time, clearly weaker code (judge: 27B wins 21 of 23 pairs, tests 24 of 24), fewer spec flaws noticed (6/16 vs 13/16), 32 rejected done claims from rewritten acceptance criteria. The RTX 5060 Ti now serves the dense Qwen3.8-27B (production chat template, 114k window) as a second coding agent until the SIEM work needs the card. Team mode: two agents on one project, each in its own git worktree, with claims, `Depends on:` / `Touches:` gating, sync with and merges into `main` by the driver, an event log and a watcher for agents blocking each other; tested with stub agents (and a single-agent regression test). First team project: `frontpage`. Frontpage round 1 by the two-agent team: 25 tasks, 302 tests, ~4.9 h, no conflicts; a live fetch worked (95 items, 0 errors) but ranking was word overlap. Package policy: pinned, hashed per-project virtualenvs built by the driver, pinned model downloads (tested against PyPI, including a tampered hash); frontpage re-planned for a pretrained sentence-embedding model. Temperature guard extended to every GPU with phone alerts (doorbell) and per-card safety stops, after the open frame's airflow put the 3090 Ti above the 5060 Ti's exhaust. Team mode hardened during frontpage round 2: no re-taking finished tasks, hotspot plans rejected, heartbeat-based claims (a restart had swapped the agents' tasks), a per-agent task size limit (the 114k agent stalled on a 14-file task), second planning and goal-check rounds (a claim spin), `agent-team restart`; each with a stub-agent scenario that fails on the earlier code. Live frontpage preview that rebuilds from main after every merge. Type-1 classifier timed on CPU only (16 E-cores, GPU servers untouched): 0.43 windows/s, about 55x slower than the 3090 Ti, same answers (type1.md). |
| 2026-10-03 | Trial: agents no longer write unit tests: end-to-end, then integration, then golden tests; code that seems to need a unit test is flagged in the hand-over for the next (fresh) session to re-read; edge cases go into the spec and the implementation; frontpage is the trial project from its iteration 125 on, its existing unit tests left in place ([agent-harness.md](agent-harness.md)). |
| 2026-10-04 | `agent-watch` rebuilt after it kept looking stuck. Measured on both frontpage agents: following the next session worked (one view had followed 39 sessions on its own), but macOS Terminal stopped following the output under the viewer's scroll region (recorded on screen) and left duplicate footer rows on resize; agent b's status bar read agent a's server (the viewer ignored `team.env`), so it showed tokens per second while b sat idle waiting for its teammate; the view went silent between sessions and while vLLM held back a long tool call (a 25 KB `write` arrived in 8 chunks). Now: Claude Code-style layout with no scroll region, an activity row (running command and its latest output line, tool call being written, thinking, between sessions, loop stopped, time since the last output), loop-log lines in the transcript, each team agent's own server, a half-written line waits for its newline instead of being dropped, escape codes in agent output removed, a status-thread race fixed. Tested end to end in a pseudo-terminal (pyte): all 52 tool headers and 20 thinking blocks of a real session in order, no footer rows in scrollback, resize, thinking toggle, typing, quit. Harness VM and AI box clocks moved from UTC to America/Chicago at 13:57 CDT (18:57 UTC) to match the operator's laptop: local timestamps from before then (loop logs, `STUCK.md`, journals as written) are UTC; ledger and team-event times carry their offset and are unaffected. Memory layout for less reading at spawn: PITFALLs move out of DECISIONS.md into a curated, searchable PITFALLS.md (77 entries were 87% of a 74 KB DECISIONS.md, which every session read whole); the agent reads its task file, PROGRESS.md (now with its own task's journal), the PITFALLS.md contents block, CODEMAP.md and git log, and searches DECISIONS.md and PLAN.md: 14.3k tokens at spawn instead of 63.5k on frontpage. New `pitfalls-sync`, `decisions-archive --show`; projects without PITFALLS.md unchanged (stub regression identical). Team plan check fixed after the planning task ran 16 sessions in a row: the per-file hotspot count ignored dependencies; `plan-schedule` replays the scheduler instead and its advice sends a plan back at most once per GOAL.md version. Team idle time measured (agent b idle 56% of its running time on frontpage, Oct 2-4: planning 14%, goal checks 13%, waiting on dependencies 28.5%) and attacked: critical-path picking with per-agent speeds (the slow card takes tasks nothing waits on), prep sessions (an idle agent writes notes for the task that starts soon; only the notes reach main), cut as soon as real work is free, with a short hand-off wait for the agent that claims the task; an old race that could run the planning task twice fixed. Tested with stub agents, a real-repo integration test and the wrapup extension against a mock Pi; deployed the same evening. |
