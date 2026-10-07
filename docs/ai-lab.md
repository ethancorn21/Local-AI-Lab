# AI Lab

The hub for the local AI lab: the GPU server, the model it serves, the isolated VM where the coding agents work, how
they are connected and secured, and what is planned. Kept up to date after every change (see the changelog at the end).
Internal addresses, VLAN numbers, account names and key material are left out on purpose.

Related notes:
- [How the harness works](how-it-works.md): the guide to the agent system, start here.
- [Agent harness reference](agent-harness.md): every rule with its reason, tests, decisions, commands.
- [Experiments](experiments.md): what was measured and what it decided.
- [Model choice](model-choice.md), [thinking-effort A/B](effort-ab.md), [type-1 log triage model](type1.md): the long
  write-ups.
- [History](history/): the first build, team mode's first run, the detailed changelog.

## Overview

| Part | Role |
|---|---|
| AI box (trusted network) | The GPU server. Model servers listen on localhost only. |
| Harness VM (isolated lab network) | The agents' own machine: the harness, the driver, the projects, a search engine for web research. |
| Link | SSH tunnels from the VM to the AI box, one per model port. The tunnel key can forward only the listed ports. |
| Operator | Laptop to VM over SSH (`agent-watch`, loop control), Telegram (replies to plain requests), or the telecloak app (start/stop, confidential projects); laptop to AI box for administration. |
| Claude | The architect: turns the operator's intent into goals and harness rules, and administers both machines through its own accounts. |

## Hardware (AI box)

| Item | Now | Notes |
|---|---|---|
| CPU | Intel i9-14900KF | Unstable under Windows (suspected Raptor Lake degradation). P-cores capped, power limited to 125 W. No hardware errors under Linux so far. |
| GPU 0 | RTX 3090 24 GB (used), slot 1 (PCIe 4.0 x16) | Installed 2026-10-06. A third agent, as fast as the 3090 Ti. Capped at 300 W (stock 350, max 400). Its memory chips sit on the back under the backplate, so it needs air there. |
| GPU 1 | RTX 5060 Ti 16 GB, a PCIe 3.0 x1 slot | Second coding agent since 2026-10-02 (dense 27B on llama.cpp). Stock 180 W. x1 only slows model loading: the model sits fully in its memory. |
| GPU 2 | RTX 3090 Ti 24 GB, slot 3 (PCIe 4.0 x4) | Production model (vLLM). Capped at 300 W since 2026-10-06 (350 W before). |
| RAM | 32 GB DDR5 | Upgrade as a 2-stick kit, not 4 sticks (two DIMMs per channel slows DDR5). |
| Board | MSI PRO Z790-P WIFI | Slot 1 PCIe 5.0 x16 (CPU), slot 3 PCIe 4.0 x4 (chipset), the rest PCIe 3.0 x1. GPU numbers follow the PCI bus, so they change when cards move; every service picks its card by UUID. |
| PSU | EVGA SuperNOVA 1300 G2 (single rail, 6 PCIe power sockets) | Enough for all three cards with the power caps. |
| Network | Onboard NIC, DHCP from the core switch | Fixed by a DHCP reservation on client ID `01` + MAC. Netplan sends the MAC (`dhcp-identifier: mac`): the default ID follows the NIC's PCI path, so moving GPUs changed it and the box got a new address. |
| Storage | 1.8 TB NVMe, LVM | Root 200 GB, a separate volume for models, ~50 GB free for snapshots. |
| Chassis | Open frame (since 2026-10-02), CPU water cooler | Risers for three cards planned. |

### Power and heat

- **Limits at boot.** A service sets the CPU and GPU power limits, reads them back, and fails if they did not take;
  the model servers depend on it, so nothing ever serves uncapped.
- **Temperature guard.** Every 15 s it logs CPU and per-GPU temperature, power, fan and throttle flags. It alerts the
  phone when a card sits at 83 C for 2 minutes, throttles, or runs its fan at 95% for 5 minutes. A card at 88 C for a
  minute gets its model server stopped; the CPU at 95 C stops all of them.
- **Observed** at the old 350 W cap: the 3090 Ti ran 64-75 C at 77-85% fan, never throttling (it slows at 94 C). In
  the open frame it sits above the 5060 Ti's exhaust, which costs a few degrees.

## Operating system

- Ubuntu, headless (no desktop: frees VRAM).
- NVIDIA's open driver from Canonical-signed packages, so Secure Boot stays on.
- Docker with the NVIDIA container toolkit. The AI box has no host firewall, so every service binds to 127.0.0.1.

## Model serving

**Production:** Qwen3.8-27B, 4-bit (W4A16 AutoRound), on vLLM with the HyperQwen patches, in Docker on the 3090 Ti.
The model is a hybrid: most layers use linear attention with a fixed-size state and only 16 use full attention, so
context memory grows slowly.

| Setting | Value |
|---|---|
| Speed | ~106 tok/s with short prompts, ~99 tok/s at ~52k tokens of context |
| Speculative decoding | MTP, 3 draft tokens, 69% accepted, ~3.1 tokens per step |
| Context | 150k per request, 200k tokens of KV cache in total |
| Prefix caching | 94% of prompt tokens served from cache in agent sessions |
| Vision | Up to 8 images per request |
| Thinking | Effort xhigh; 16k tokens per response, ended with a short wrap-up sentence instead of a bare end tag ([why](experiments.md#thinking-cap)) |
| Quality | Perplexity +1.27% against an 8-bit reference on the lab's own code |

### Benchmarks on the 3090 Ti (2026-09-27)

llama.cpp generation speed (tok/s) at 0 / 32k / 64k tokens of context:

| Quant | Speed | Largest context that fits (no MTP / MTP) | KL divergence vs 8-bit |
|---|---|---|---|
| IQ4_XS | 49 / 40 / 34 | 262k / 196k | 0.031 |
| Q5_K_M | 38 / 32 / 28 | 131k / 82k | 0.0105 |
| Q6_K | 34.5 / 29 / 25 | 114k | 0.0087 |

| Engine | Speed short / long | Prompt reading | Quality vs 8-bit |
|---|---|---|---|
| **vLLM HyperQwen (chosen)** | **106 / 99 tok/s** | ~1,110 tok/s | +1.27% perplexity |
| NInfer-3090 | 65 / 55 tok/s | ~860 tok/s | about +0.8% |
| llama.cpp IQ4_XS + MTP | 63-72 / 56-65 tok/s | ~1,500 tok/s | KL divergence 0.031 |

Splitting one model across two cards speeds a single request up only 16-35% (the cards sync over PCIe twice per
layer); two separate copies give twice the work. So the lab runs one model copy, and one agent, per card.

### Second card: RTX 5060 Ti (2026-10-01)

- It runs the same dense 27B (IQ4_XS, all on the card, 114k window) at ~28 tok/s, as the second agent of team mode.
- A mixture-of-experts model (Qwen3.6-35B-A3B) ran three times faster (84 tok/s) but wrote clearly weaker code and
  kept rewriting the human's acceptance criteria ([experiments](experiments.md#a-mixture-of-experts-model-on-the-rtx-5060-ti)).
- Speed follows memory bandwidth divided by the bytes read per token: the dense model reads all ~14 GB per token, the
  mixture-of-experts model only its ~3B active parameters.
- Server: `llama-5060ti.service` ([config](../server/llama.cpp/llama-5060ti.service)), pinned to the card and the
  P-cores. The 5060 Ti cannot share a model with the 24 GB cards (the model splits across 2 or 4 identical cards only).

### Ports and tunnels

Each model server listens on localhost on the AI box and appears at the same port on the VM through its own SSH tunnel
(`llm-tunnel@<port>`, [example](../harness/system/llm-tunnel@.service.example)). Adding a model is one allowlist entry
on the AI box plus one tunnel unit on the VM, with no firewall change.

| Port | Service |
|---|---|
| 8080 | vLLM on the 3090 Ti (production) |
| 8081 | vLLM on the 3090 (agent c) |
| 8082 | llama.cpp on the 5060 Ti |
| 8090 | Reserved: the type-1 log triage model |

### Lessons from serving

1. Measure placement, don't assume: each GB of model offloaded to system RAM cost ~14 ms per token.
2. 4-bit is the right trade for an agent: 8-bit halves the speed for about 1% perplexity.
3. A bigger context only helps if the workflow uses it.
4. The server's own metrics (speculative acceptance, cache hits, preemptions) are the best health check.

## Network and security

- **Segmentation.** The AI box is on the trusted network; the harness VM is in an isolated lab network. The firewall
  allows only VM to AI box over SSH. The model API is never exposed.
- **The tunnel key does one thing:** forward the listed model ports, from the VM's address only, with no shell.
- **Separate identities** for the operator, Claude, the model service, the tunnel and the agents, so every action in
  the auth log is attributable and each can be revoked on its own.
- **The agents' permissions on their VM:** `sudo apt-get` (root-equivalent there, a deliberate choice) and web
  research through a local search engine whose results are labelled untrusted. Locking down the VM's internet access
  was considered and decided against: the network isolation is the boundary.
- **Requests to the human travel encrypted.** The bot token and keys live on the AI box, never on the VM the agents
  control; the VM can only call a relay ([details](agent-harness.md#requests-to-the-human)). Because the agent now
  chooses the words the human reads, requests are treated as untrusted and shown as plain text.
- **Supply chain.** The agent harness once replaced itself at runtime with a renamed package, so self-updates are off
  and versions pinned. Python packages are pinned with hashes; model weights come only at pinned revisions with
  checksums.

## Harness VM

- Ubuntu 24.04, 6 vCPU, 15 GB RAM, 48 GB disk.
- Services: the model tunnels, SearXNG (localhost only) for web search, a text extractor for fetched pages.
- Tools: the Pi coding agent with the lab's extensions, the driver and its helpers, the operator tools, Playwright.

## Projects

| Project | What | Status |
|---|---|---|
| sshreport | An sshd log analyzer, the first loop test | Done 2026-09-25 |
| hollowdeep | A browser game, three design versions (dungeon crawler, twin-stick, top-down looter shooter) | v3 in progress; ~7,400 lines of code, ~12,200 of tests after ~40 hours |
| frontpage | A personal reading feed ranked by a local embedding model, with no algorithm pits. The first team-mode project | Goal met 2026-10-04 (64 tasks, ~8,100 lines of code, ~12,000 of tests); new sprint since the operator's reviews |

## Plans

- **The 5060 Ti** goes to the SIEM work and the type-1 log triage model when that starts.
- **Less idle time in team mode:** a streamed plan; later a bigger model on both 24 GB cards for planning.
- **Open-frame rig** with risers for three cards; re-check temperatures after the move.

## Changelog

The full day-by-day record: [history/changelog-detailed.md](history/changelog-detailed.md).

| Date | Change |
|---|---|
| 09-24 | Project start: the Windows box repurposed for a local coding agent. |
| 09-25 | Headless Ubuntu, llama.cpp serving the model, the harness VM and its tunnel; Pi chosen; the first autonomous loop. |
| 09-26 | Session ledger, journal archive, enforced hand-over, vision. |
| 09-27 | RTX 3090 Ti in; power limits at boot; vLLM HyperQwen in production; agents may split tasks, install packages, research the web. |
| 09-28 | Hand-over at 120k; projects start from `GOAL.md`; requests to the human; experiments on hand-over notes, Pi vs Oh My Pi, two agents per GPU. |
| 09-29 | The doorbell becomes an encrypted two-way relay (telecloak); type-1 log triage started. |
| 09-30 | Hang handling; the thinking-effort experiment built. |
| 10-01 | Thinking-effort results; RTX 5060 Ti in; "refactor first, then add". |
| 10-02 | Team mode; frontpage, the first team project; pinned per-project Python packages; per-GPU temperature alerts. |
| 10-03 | No more unit tests: end-to-end, integration and golden tests only. |
| 10-04 | `agent-watch` rebuilt; `PITFALLS.md` split from the journal (14k tokens read at start-up instead of 63k); critical-path scheduling and prep for idle agents. |
| 10-05 | Model choice test: the 27B stays; capped thinking now ends with a wrap-up sentence. |
| 10-06 | Request deadline; `GOAL.md` numbers as intuition; one project per GPU; thinking cap back to 16k; 3090 Ti capped at 300 W; the second 3090 installed in slot 1 at 300 W (3090 Ti to slot 3, 5060 Ti to an x1 slot), DHCP client ID = MAC; agent c's vLLM on the 3090 (8081) and `agent-team add`, three agents on frontpage; OS updates on both machines; takeover (an idle fast agent takes the slow card's critical task, with its work); requests to the human in plain text, answered by replying in Telegram, except for projects marked confidential. |
