# Building a Local Autonomous Coding Agent on One 16 GB GPU

> Update 2026-09-28: this is the write-up of the first build session and is partly out of date (GPU, model server, loop). The current architecture, decisions and experiments are in [Local Coding Agent Harness - Architecture and Decisions](../agent-harness.md).

A write-up of one build session: a local Qwen3.8-27B model serving a coding agent that runs unattended in a loop, with externalized memory, a deterministic watchdog, and a network design that treats the agent as untrusted. Internal addresses, VLAN numbers, account names and key material are replaced with generic placeholders.

## Goal

- Run a capable open-weight coding model entirely on home hardware, squeezing maximum intelligence, context and speed out of a single consumer GPU.
- Let an agent work through a queue of coding tasks for days without supervision.
- Make sure it can never get stuck in a multi-day loop, and that a compromised agent cannot reach anything important.
- Later: escalate to a cloud model (Claude) when the local model is stuck for ~12 hours.

## Architecture

| Component | Where | Role |
|---|---|---|
| Model server | "AI box" on the trusted LAN (i9-14900KF, 32 GB DDR5, RTX 4080 SUPER 16 GB, Ubuntu 26.04) | llama.cpp `llama-server`, bound to `127.0.0.1` only, as a sandboxed systemd service |
| Harness VM | Separate Ubuntu VM in the untrusted, internet-facing VLAN | Runs the agent harness (Pi) and the loop driver as an unprivileged user |
| Link | SSH tunnel from VM to AI box | VM's `localhost:8080` forwards to the model server; nothing else is reachable |
| Operator access | Laptop -> VM over SSH, landing in tmux | Watch the agent live, prompt it by hand, or attach to long runs |

## Model serving: getting 27B to fit in 16 GB

**Model:** Qwen3.8-27B, Unsloth `UD-IQ4_XS` quant (14.25 GB). Qwen3.8 is a hybrid model: most layers use DeltaNet (linear attention with a fixed-size state) and only 16 layers use full attention, so the KV cache grows far slower with context than in a normal transformer.

**Why IQ4_XS and not Q4_K_M:** earlier testing measured KL divergence of 0.018 between them (near-identical token choices) with equal perplexity on the target domain. Importance-matrix ("I") quants spend bits on the weights that matter most. On 16 GB, the 2.2 GB saved decides whether the model fits: Q4_K_M spilled to RAM and ran at 13-19 tok/s, IQ4_XS stays on the GPU at ~42 tok/s.

**Linux vs Windows (same hardware):**

| Metric | Windows | Linux (headless) |
|---|---|---|
| Generation (short prompt) | ~40 tok/s | 43.5 tok/s |
| Prompt processing | ~1,500 tok/s | 2,086 tok/s |
| Max context fully on GPU | 98k | 114k |

The gain came from dropping the desktop compositor and WDDM (~0.4 GB of VRAM back, less driver overhead).

**Context sweep (q4_0 KV cache, needle-recall test passed at every size):**

| Context | Placement | Speed fresh -> 60-90% full |
|---|---|---|
| 98k | All GPU | 42 -> 30-33 tok/s |
| **114k** | **All GPU (chosen)** | **42 -> 32 tok/s** |
| 131k | 1 FFN layer in RAM | 37 -> 28 tok/s |
| 164k | 8 FFN layers in RAM | 26 -> 17 tok/s |
| 262k | 22 FFN layers in RAM | 16 -> 12 tok/s |

**Rule of thumb learned:** every generated token reads every weight once. VRAM feeds ~13 GB in ~24 ms; anything in system RAM crosses a ~70 GB/s bus. On this box, **each GB offloaded to RAM adds ~14 ms per token**, which is why offloading even 0.65 GB dropped speed from 42 to 30 tok/s. 114k fully on GPU beat 131k with offload: 13% less context for ~13% more speed.

**Other serving details:**
- NVIDIA 610-open driver from Ubuntu's prebuilt, Canonical-signed module packages, so Secure Boot stays on with no MOK enrollment. CUDA toolkit installed alone (not the `cuda` meta-package, which would replace the signed driver).
- llama.cpp built from source for sm_89 (4080 SUPER) and sm_120 (a Blackwell card on order), pinned to the commit used in earlier Windows benchmarks.
- `--fit off -ngl 99`: the server fails at load rather than silently spilling to RAM.
- systemd hardening: dedicated service user, `ProtectSystem=strict`, `ProtectHome`, `NoNewPrivileges`, write access only to the model directory.
- Disk: default Ubuntu LVM root was only 100 GB of a 1.8 TB NVMe. Root grown to 200 GB, a separate volume for models and apps, ~50 GB left unallocated for LVM snapshots.

## Security design

**Separate identities for every actor.** The operator, Claude (the cloud assistant that did the build), the model service, the tunnel, and the coding agent each have their own account and key. Every action in `auth.log` is attributable, and any one of them can be revoked by deleting one line or locking one account.

**The tunnel key can do exactly one thing.** The VM's tunnel account on the AI box has a no-login shell, and its `authorized_keys` entry is:

```
from="<vm-ip>",restrict,port-forwarding,permitopen="127.0.0.1:8080",command="/bin/false" ssh-ed25519 AAAA... tunnel@vm
```

Verified: attempts to forward any other port return `administratively prohibited`. The firewall allows only VM -> AI box TCP/22, so the model server's unauthenticated API is never exposed to the network. Measured tunnel overhead: zero (42.4 tok/s via tunnel and locally; a token is a few hundred bytes).

**Firewall mistakes found and fixed during the build:**
- A pass rule with **source port 22** instead of any. SSH clients use random ephemeral source ports, so the rule never matched and traffic fell through to the block rule. Almost every rule should have source port "any".
- An auto-generated rule allowed the untrusted VLAN to reach **every service on the firewall itself** (GUI, SSH). Restricted to DNS/DHCP only; re-tested from the VM afterwards.

**Other hardening:**
- sshd `LogLevel VERBOSE` on the AI box so logs record which key fingerprint was used, including failed attempts (the default level only logs a vague `Connection closed ... [preauth]`).
- Host keys cross-verified between the laptop's pinned key, the VM's scan and the key file on the box, so the VM's first connection was not trust-on-first-use.
- The coding agent runs as an unprivileged user with no sudo, on a VM with a clean snapshot.

**Supply-chain finding:** the Pi harness **replaced itself at runtime** during a test run: it uninstalled `@mariozechner/pi-coding-agent` and installed the newest `@earendil-works/pi-coding-agent` from npm, without being asked and without `--ignore-scripts`. Investigation showed it was legitimate (the old package is officially deprecated in favour of the new scope, same maintainers, documented in the changelog), but an unattended agent host must never change its own code. Self-update and version checks are now disabled in both harnesses, versions are pinned, and upgrades are done deliberately with `--ignore-scripts`.

## Harness bake-off: Qwen Code vs Pi

Four coding tasks, two repetitions each, graded by **hidden tests** the agent could not see (it only got the spec and a few visible tests), so an agent could not pass by writing code that only satisfies its own tests.

| Task | What it stresses |
|---|---|
| bugfix | Finding three planted bugs plus an input-mutation requirement |
| feature | Consistent change across 3 files: dataclass field, store methods, error atomicity, JSON round-trip, CLI flags |
| ttlcache | Precise spec with edge cases: expiry boundary, expired-before-LRU eviction, O(1) operations |
| authlog | sshd log parsing: padded days, IPv6, odd usernames, inclusive sliding window for brute force, password spray |

| Harness | Hidden tests | Perfect runs | Total time |
|---|---|---|---|
| Qwen Code 0.24.5 | 83/84 | 7/8 | 46 min |
| **Pi 0.87.1** | **84/84** | **8/8** | **44 min** |

Quality was effectively a tie (Qwen Code's one miss, evicting a valid entry before dropping an expired one, did not repeat in rep 2). **Pi won on fit:** a leaner system prompt leaves more of the 114k window for code, and it has compaction hooks and a JSON/RPC mode for driving it from a script.

## The autonomous loop

**Core idea:** don't run one long session that keeps compacting. Run many short ones. Each iteration a **fresh** agent spawns, reads the memory files, does one step, writes memory, commits, and exits. Compaction becomes a rare safety net instead of the thing memory depends on.

**Memory, three tiers per project:**

| File | Question it answers |
|---|---|
| `PROGRESS.md` | What am I doing right now, and what is the exact next step? |
| `DECISIONS.md` | Where did I get stuck, what failed and why, what was chosen over what (append-only) |
| git history | What was done in the past (one commit per verified step, message says what and why) |
| `tasks/NNN-*.md` | The work queue; first line is `Status: open / in-progress / done / blocked` |

`AGENTS.md` (auto-loaded by the harness) holds the protocol: orient, check `DECISIONS.md` for dead ends before choosing an approach, do one step, run tests, write memory, commit, stop. A **stuck rule**: after the 3rd failed attempt at the same blocker, mark the task blocked, document what a human or stronger model needs to know, and move on.

**The driver is not an agent.** `agent-loop` is ~100 lines of bash with no model in it. It picks the next task (in-progress first, then the lowest-numbered open), launches Pi with a timeout, commits any leftovers as `[driver]` (which don't count as progress), and stops after N iterations without an agent commit, writing `STUCK.md`. It also **verifies every "done" claim**: all acceptance boxes ticked and the test suite passing when the driver runs it itself; otherwise it reopens the task and appends the reason to `DECISIONS.md` for the next agent to read.

**Key principle, borrowed from SOC work:** loop detection and done-verification must live **outside the model**. A looping model has lost track of the fact that it is looping; asking it to notice is asking the failed component to report its own failure. It's the same reason you judge a possibly compromised host from off-box telemetry instead of trusting its own health report. A bash `if` on an exit code can't be persuaded that the tests are "basically passing".

**Watching it:** a small viewer (`agent-watch`) renders the agent's event stream like Claude Code: streamed thinking, tool calls, and a pinned status bar with context used vs window and live tok/s, taken from llama-server's `/slots` endpoint (the model server's own numbers). The viewer only reads the iteration log files, so closing it can't affect the agent.

## Results so far

- **Test project (3 tasks, sshd log analyzer):** done in 19 minutes, 43 passing tests, found all three planted attacks including the HIGH-severity success-after-brute-force. `DECISIONS.md` recorded the why behind choices, and the agent spotted and documented an inconsistency in the spec.
- **Stress test (in progress):** "Hollowdeep", a browser Diablo-like (procedural floors, 4 enemy types plus champions and a phased boss, rarity-tiered loot, procedural Web Audio music that speeds up with danger), specified on one page and split into 13 tasks. The agent is working through the queue unattended. Early observations: tasks legitimately span several sessions and the queue hands the same task back until it's done; an agent discovered that `node --test <dir>` doesn't work on Node 22 and flagged it as IMPORTANT in `PROGRESS.md` for its successors; one task was marked done without its acceptance boxes ticked, which is what prompted the done-verification in the driver.

## Lessons

1. **Measure placement, don't assume it.** The offload math (bytes read per token / bus bandwidth) predicted the measured slowdown almost exactly.
2. **Headless Linux is free performance** for single-GPU inference: more VRAM and faster prompt processing.
3. **Hidden tests** are the only honest grade for an agent; its own tests only check what it thought to check.
4. **Never put the display in the agent's pipeline.** A Ctrl-C in the viewer once killed the agent via a broken pipe (EPIPE); decoupling fixed it.
5. **Pin everything on an autonomous host.** Self-updating tools change behaviour mid-run and are the delivery path for a hijacked package.
6. **Supervision belongs outside the model.** Memory makes loops rarer; only a deterministic watchdog makes them bounded.
7. **Operational slips worth remembering:** `pkill -f pattern` over SSH matches its own command line and kills itself; overwriting a running bash script can corrupt it (swap with `mv` instead); a `sudo` password prompt swallows pasted lines.

## Next

- Test the stuck rule with a deliberately impossible task; add "never invent tasks, stop on an empty queue".
- Notifications (ntfy) and tuned thresholds for the watchdog.
- Escalation to Claude after ~12 hours stuck, with `STUCK.md` + `DECISIONS.md` as the handoff.
- Second GPU (RTX 5060 Ti 16 GB, 32 GB total): move to a Q6/Q8 quant or the full 262k context.
