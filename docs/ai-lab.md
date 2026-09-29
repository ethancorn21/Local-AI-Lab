# AI Lab

The hub note for the local AI lab: the GPU server, the model it serves, the isolated VM where the coding agent works, how they are connected and secured, what was measured, and what is planned. Kept up to date after every change (see the changelog at the end). Internal addresses, VLAN numbers, account names and key material are left out on purpose.

Related notes:
- [Architecture](architecture.md): how the work is split between the cloud architect, the orchestrator script and the per-task local agents.
- [Local Coding Agent Harness - Architecture and Decisions](agent-harness.md): the agent side (loop, memory, tasks, experiments, operating commands).
- [Local Autonomous Coding Agent Build (first build, 2026-09-25)](history/first-build-2026-09-25.md): the first build session (2026-09-25), partly out of date.

## Overview

| Part | Role |
|---|---|
| AI box (trusted LAN) | GPU server running the model. Serves only on localhost. |
| Harness VM (untrusted, isolated lab VLAN) | The coding agent's own machine: the agent harness, the loop driver, its projects, a search engine for its web research. |
| Link | An SSH tunnel from the VM to the AI box whose key can forward exactly one port (the model API). |
| Operator | Laptop -> VM over SSH into tmux (live agent view, loop control); laptop -> AI box for administration. |
| Claude | Architect/designer: turns the operator's intent into designs, seed tasks and harness rules; administers both machines through its own accounts. |

## Hardware (AI box)

| Item | Now | Notes |
|---|---|---|
| CPU | Intel i9-14900KF | History of instability under Windows (suspected Raptor Lake degradation); P-cores capped, power limited to 125 W (PL1 = PL2). No hardware errors seen under Linux so far. |
| GPU | 1x RTX 3090 Ti 24 GB (Ampere) | Replaced the RTX 4080 SUPER 16 GB on 2026-09-27. Power cap 350 W. |
| GPUs coming | 2nd RTX 3090 Ti 24 GB, RTX 5060 Ti 16 GB | 64 GB VRAM total. The 5060 Ti cannot share a model with the 3090 Tis (the model's 4 KV heads allow splitting across exactly 2 or 4 identical cards). |
| RAM | 32 GB DDR5 | If upgraded: a 2-stick kit (2x32 or 2x48), not 4 sticks (two DIMMs per channel slows DDR5 and stresses the memory controller). |
| Board | MSI PRO Z790-P WIFI | Slot 1 PCIe 5.0 x16 (CPU), slot 3 PCIe 4.0 x4 (chipset), the rest PCIe 3.0 x1. |
| PSU | EVGA SuperNOVA 1300 G2 (single rail, 6 PCIe power sockets) | Plan for three cards: 3090 Ti A on 2-3 sockets, 3090 Ti B on 2, 5060 Ti plus its powered riser on 1. EVGA G2/G3/G5/P2/T2 modular cables are cross-compatible. The current card runs on 2 cables (one daisy-chained); the 350 W cap keeps it well within that. |
| Storage | 1.8 TB NVMe, LVM | Root 200 GB, a separate volume for models and apps, ~50 GB unallocated for snapshots. |
| Chassis | Case with a CPU water cooler | Rear radiator fan hub has no power (no Molex cable); an open-frame mining rig with risers is planned for the three cards. |

### Power and heat

- Power limits are applied at boot by a service that the model server and Docker depend on: nothing serves uncapped. It sets CPU PL1/PL2 to 125 W, GPU persistence mode and the 350 W GPU cap, reads them back, and fails if they did not take.
- A temperature logger records CPU package, hottest core, GPU temperature, power, fan and load every 15 s. If the CPU's hottest core stays at 95 C or the GPU at 88 C for a minute, it stops the model server and logs an OVERHEAT event.
- Observed (blower fans off, agent loop running): CPU hottest core 38-76 C, GPU 44-80 C at ~348 W, GPU fans up to ~89%, no thermal throttling (the card slows down at 94 C). A 300 W cap is an option if it runs warmer in the open frame.

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

### Lessons from serving

1. Measure placement, don't assume: each GB of model offloaded to system RAM cost ~14 ms per token on the old card.
2. 4-bit is the right trade for an agent: 8-bit halves the speed for about a 1% perplexity gain, and an independent agent benchmark found 8-bit no better than 4-bit on coding tasks.
3. Bigger context only helps if the workflow uses it; the agent hands over at 120k of the 150k window.
4. The model server's own metrics (speculative acceptance, prefix-cache hits, preemptions) are the best health check.

## Network and security

- **Segmentation:** the AI box is on the trusted LAN; the harness VM is in an isolated lab VLAN. The firewall allows only VM -> AI box on SSH. The model API itself is never exposed to the network.
- **The tunnel key can do one thing:** forward the model port, from the VM's address only, with no shell. Other forwards are refused ("administratively prohibited").
- **Separate identities** for the operator, Claude, the model service, the tunnel and the agent, so every action in the auth log is attributable and each can be revoked on its own. SSH logs record the key fingerprint used.
- **The agent's permissions (its own VM):** it may install system packages (`sudo apt-get` only, which is root-equivalent on that VM, a deliberate decision) and research the web through a local search engine and a page fetcher that labels results as untrusted. Egress lockdown was considered and decided against; the VLAN isolation is the boundary.
- **The doorbell (agent -> human pings):** the Telegram bot token lives on the AI box, not on the VM the agent controls. The VM can only trigger a fixed message through an SSH key whose forced command is the doorbell (source address pinned, no PTY, no forwarding, arguments ignored; verified that extra command text, a PTY and port forwards are all refused). Nothing about the agent's work leaves the lab; the request itself is read on the VM.
- **Supply-chain lessons:** the Pi harness once replaced itself at runtime with a renamed package (legitimate, but an unattended host must never change its own code: self-updates are now off and versions pinned); and before it had package rights, the agent fetched unsigned Debian packages over plain HTTP to get test dependencies. That is why it now has proper apt access instead.

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

## Plans

- **Second 24 GB GPU (the 3090 Ti order was cancelled; a used plain RTX 3090 is the value pick):** two agents on the same project, one model copy per card (measured: two agents on one card give only ~1.06x). Design in the harness note. A pair of RTX 5060 Ti 16 GB costs about the same as one 3090 but only runs the model by splitting it across both cards over this board's x16 + x4 links; it would at best tie a 3090 for one agent, so it is worth it only for a small-model tier of agents (untested).
- **RTX 5060 Ti:** its own small model or helper jobs; not decided. Revisit once two agents run.
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
