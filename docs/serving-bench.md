# Serving bench, October 2026: what the box can serve once the harness is gone

Status (2026-10-24): decided (Decision, below). Done: speed, long context, power, switch times, coding quality, the
4-bit Flash-Next, big's coding order, main's agents at 40k and 128k, the swarm (every model at the pool's fit, the
27B and the 5060 Ti included), main and swarm at once, and the 27B's check of every swarm's reports. Running: the
check again with its prompt rewritten (Swarm, last paragraph). Sections marked _pending_ fill in as they finish.

## The question

On 2026-10-17 the agent harness was scrapped. In its place: one local-compute service that Claude (through MCP) and
the GrokBots (through HTTP) call with "setup X, do this", with work queued. Three setups:

1. **big:** the largest model the box can run, across all three cards, for the hardest coding.
2. **main:** one strong model across both RTX 3090s at a high quant and long context; the RTX 5060 Ti stays free for
   small jobs. In the human's words, throughput no longer matters as much as work getting done across several systems.
3. **swarm:** a small model serving about 100 scouts at once: "point it at something and like 100 local agents go
   swarm it".

For each setup the bench answers: which model and engine, how many agents at what speed per agent and in total, how
good the work is, and what a switch between setups costs (the scheduler pays it every time it changes setup).

## Hardware and limits

| Part | What | Why it matters here |
|---|---|---|
| GPU 0 | RTX 3090, 24 GB, PCIe 4.0 x16, 275 W limit | The fast link |
| GPU 1 | RTX 3090 Ti, 24 GB, PCIe 4.0 x4 (chipset), 275 W limit | A split model's traffic to it crosses the chipset |
| GPU 2 | RTX 5060 Ti, 16 GB, PCIe x1 | Anything that streams to it is slow |
| CPU | i9-14900KF (P-cores capped at 5.7 GHz) | Strata computes some experts on it; vLLM keeps it busy |
| RAM | 30 GB usable | Less than any Flash-Next quant on disk (64-104 GB) |
| Disk | Crucial P3 Plus 2 TB NVMe (DRAM-less) | Flash-Next experts that fit nowhere else are read from it |

## Arms

| Arm | Model | Engine | Cards | On disk |
|---|---|---|---|---|
| fp8-27b | Qwen3.8-27B, official 8-bit (FP8) | vLLM 0.29 (HyperQwen image), tensor parallel 2, fp8 KV cache, prefix cache | 3090 + 3090 Ti | 29 GB |
| fp8-27b-mtp | the same, with Qwen's MTP drafter (3 tokens ahead) | as above | 3090 + 3090 Ti | 29 GB |
| fn-iq2xs-2 | Qwen3.8-Flash-Next (125B mixture of experts, 6B active), IQ2_XS | Strata @477ac13 | 3090 + 3090 Ti | 64 GB |
| fn-iq3s-2 | Flash-Next IQ3_S | Strata | 3090 + 3090 Ti | 78 GB |
| fn-q4kxl-2 | Flash-Next UD-Q4_K_XL (Unsloth) | Strata | 3090 + 3090 Ti | 104 GB |
| fn-iq3s-2-p2 | IQ3_S, Strata decoding two requests together (`"parallel": 2`) | Strata | 3090 + 3090 Ti | 78 GB |
| fn-iq3s-3 | Flash-Next IQ3_S | Strata | all three | 78 GB |
| fn-q4kxl-3 | Flash-Next UD-Q4_K_XL | Strata | all three | 104 GB |
| a3b-awq | Qwen3.6-35B-A3B, 4-bit AWQ | vLLM, tensor parallel 2 | 3090 + 3090 Ti | 24 GB |
| a3b-fp8 | Qwen3.6-35B-A3B, 8-bit | vLLM, tensor parallel 2 | 3090 + 3090 Ti | 35 GB |
| q9b-bf16 | Qwen3.5-9B, bf16 | vLLM, tensor parallel 2 | 3090 + 3090 Ti | 19 GB |
| q4b-bf16-dp | Qwen3.5-4B, bf16, one copy per card | vLLM, data parallel 2 | 3090 + 3090 Ti | 9 GB |
| _Added after the first results (see Lessons):_ | | | | |
| fp8-27b-pp | the 8-bit 27B split by layers (pipeline parallel 2) instead of tensor parallel, 131k context (262k did not fit); with MTP it cannot run (vLLM's MTP drafter does not support pipelines) | vLLM | 3090 + 3090 Ti | 29 GB |
| fp8-27b-pp3 | the same over three cards, 262k, 26 / 12 / 26 layers, the 5060 Ti holding the middle 12 | vLLM | all three | 29 GB |
| w4-27b-tp2 | production's 4-bit 27B build (HyperQwen launcher, MTP), one copy over both 3090s, 262k context | vLLM (HyperQwen), tensor parallel 2 | 3090 + 3090 Ti | 14 GB |
| w4-27b-tp2n, -tp2m1 | w4-27b-tp2 without the drafter, and with one draft token instead of three (added when -tp2 fell past 8 agents) | vLLM (HyperQwen), tensor parallel 2 | 3090 + 3090 Ti | 14 GB |
| w4-27b-tp2r4160, -tp2m1r4080 | -tp2 and -tp2m1 with a Mamba checkpoint every five blocks instead of every block (added when both stalled at 6 agents of 128k) | vLLM (HyperQwen), tensor parallel 2 | 3090 + 3090 Ti | 14 GB |
| w4-27b-x2 | the same weights, one copy per card, as production runs (two servers, agents split between them) | vLLM (HyperQwen) | 3090 and 3090 Ti | 14 GB each |
| w4-27b-tp2b, -x2b | as the two above in HyperQwen's batch mode (no drafter, int8 activations, up to 128 seats), for the swarm | vLLM (HyperQwen) | as above | 14 GB |
| fn-iq3s-2-bm | IQ3_S decoding two requests together with MTP kept (`--batch-mtp`, serial windows) | Strata | 3090 + 3090 Ti | 78 GB |
| fn-iq3s-2-b4 | IQ3_S in Strata's setup for several conversations: 4 slots decoding in pipelined groups, hand split, trimmed stages, 64k per conversation | Strata | 3090 + 3090 Ti | 78 GB |
| fn-iq3s-2s | IQ3_S with the split set by hand at layer 27, each card loading only its own layers (`--trim-stage-weights`) | Strata | 3090 + 3090 Ti | 78 GB |
| fn-iq3s-2t | -2s plus the overlap options (`--adapt-async 1 --pipeline-windows 2`, rotated exchange, fused prefetch) | Strata | 3090 + 3090 Ti | 78 GB |
| fn-iq3s-2h, fn-q4kxl-2h | -2s with the 5060 Ti as a helper expert cache (`--expert-cache-device1 auto --remote-expert-opt`): layers on the 3090s, colder experts on the 5060 Ti ahead of RAM and disk | Strata | all three | 78 / 104 GB |
| fn-q4kxl-2t | UD-Q4_K_XL with the hand split and trimmed stages | Strata | 3090 + 3090 Ti | 104 GB |
| q4b-5060 | Qwen3.5-4B alone on the 5060 Ti, as a swarm next to a main model | vLLM | 5060 Ti | 9 GB |
| q9b-5060 | Qwen3.5-9B on the 5060 Ti, Red Hat's 8-bit checkpoint, KV in 8 bits (18 GB in bf16 does not fit 16, and vLLM's own fp8 loads the bf16 model first; bf16 KV would leave ~110k tokens) | vLLM | 5060 Ti | 10 GB |
| combo-main-q4b | main as decided (w4-27b-tp2m1r4080, below) and q4b-5060 at once, both loaded together: does running main and swarm side by side slow either | vLLM (both) | all three | 23 GB |

Left out on purpose: the production 27B (4-bit W4A16 on one card, HyperQwen with MTP) is not retested; its numbers
come from production and the 2026-10-05 [model choice](model-choice.md) test, and it is the reference the 8-bit 27B
must beat to justify a second card. Flash-Next at Q5 and Q8 do not fit (RAM plus VRAM is under 80 GB). The 27B is
not a swarm candidate: a swarm wants many small requests, not one strong one. A 9B with one copy per card was dropped
before it ran: 18 GB of weights per card leaves about 4 GB of KV cache each, a fifth of the tensor-parallel pool.

How Strata places Flash-Next: the layers are split across the cards (the faster card last), each card keeps a cache
of the most-used experts in VRAM, a budget of experts sits in locked system RAM, and the rest are read from the
model files on the NVMe drive (or the page cache) when a token routes to them. IQ3_S on the two 3090s keeps 17.3 GiB
of experts in VRAM (88% of lookups hit); UD-Q4_K_XL on three cards keeps 4.8 GiB in VRAM (69% hit), 8 GiB in RAM (the
budget Strata's setup picks for a 32 GB PC, not a limit of the box: see Q4 off the drive), and reads the rest from disk.

## Method

All scripts are in `server/bench/`. Every arm starts through `bench-arms.sh`, which also times the switch: seconds
from `docker run` to the first token of a tiny request. Requests use the production settings (thinking on at effort
`xhigh`, temperature 1.0, top_p 0.95, top_k 20) unless a row says otherwise.

- **Speed** (`bench-load.py single`): decode tok/s on a short prompt and on 50k and 175k token prompts, cold (a random
  first line defeats the prefix cache) and again cached.
- **Agents at once** (`bench-load.py load`): 1 to 16 simulated coding agents for 3 minutes each. An agent's prompt
  is 8k tokens every agent shares plus 7k of its own; it thinks and answers up to 3,000 tokens, appends its answer
  and goes again until its conversation passes 40k, then starts over. Reported: total tok/s inside the window, each
  request's decode speed while it streams, each agent's share (its tokens over the window, waits included), time to
  first token, and how full vLLM's KV cache got. Each agent starts partway through its conversation
  (`--pregrow`: a random length between 15k and 37k), and the window opens once every agent is streaming or after 5
  minutes. The first version of this test had three faults, all found on 10-18 when the 8-agent run came back with
  no per-agent numbers: it counted only requests that finished inside the window (at 8+ agents an answer outlasts
  3 minutes), it let each level's open requests run on into the next level, and with every agent starting fresh, the
  window saw only the first turn or two of a conversation that takes about 20 minutes to reach 40k, so the KV cache
  never carried its working load. The fix cuts every open request when the window closes and waits for the server
  to go idle before the next level. A fourth fault surfaced in the first Strata run after it (fn-q4kxl-2h: 0 tok/s
  at 6 agents): every Strata reply says `Connection: close`, Python's HTTP client then lets go of the socket, and
  the cut found nothing to close, so Strata ran each cut request to its end, into the next level's window. The
  engine log showed it: every request generated its full length, none was cancelled. The cut now keeps its own
  handle on the socket, and Strata (which has no metrics page) counts as idle once a one-token request comes back,
  since it serves requests in turn. Both were checked against a stub server that answers the way vLLM does and the
  way Strata does, before and after the fix. The candidate arms were rerun with the fixed test, all with agents
  starting mid-conversation (at first only the 27B arms did, which would have spared Strata's agents the longer
  prompts the 27B's paid for in the same table); rows kept from the first version are marked as such. The caps are
  nominal: the test sizes prompts at 3.6 characters a token, and the pregrown prompts (code and prose mixed) came to
  about 3.1, so a "40k" conversation reached about 46k real tokens. That was harmless until a cap met the server's
  own limit: the first 64k sweeps on one copy per card (64k served) sent prompts past it, got HTTP 400 back and
  resent the same prompt for the rest of the window (203 errors in one level). Since 10-19 each agent asks the server
  to count its prompt (vLLM's `/tokenize`) and trims it to fit, a failed request starts the conversation over, and
  each row says how many agents were trimmed; the failed rows are not used. The owner's floor, 10-19: "16k context is
  useless, so is 32k. I would say 64k is the minimum". The main candidates were measured to 40k first and again with
  128k conversations.
- **Long context, lookups** (`longctx.py`): 12 questions per depth (31k, 124k, 191k tokens of the frontpage project's
  own code) whose answer sits in one place: what a constant is set to, a function's default argument.
- **Long context, whole-prompt questions** (`longctx.py build-hard`): 10-15 questions per depth (31k, 54k, 124k,
  191k) whose answer is spread over the prompt: every function that calls X (callers); every function with a
  parameter named Y across files (takes); the one project function, defined in another file, that function Z calls
  (crosscall). Answers come from Python's own parser over the same files, so the grade is objective: list answers
  score by F1 over `path:function` entries, crosscall is right or wrong.
- **Coding** (`lcb-quick.py` with the private held-out set): the 14 tasks of the model-choice test (8 practical, 6
  algorithmic, 11 hidden tests each, every reference cross-checked against a brute-force solution), 3 attempts per
  task with different seeds, 3 at once, a 16k thinking cap ended with the wrap-up sentence on both engines.
- **Coding, harder** (`lcb-quick.py`, `LCB_N_HARD=24 LCB_N_MED=0`): 24 hard AtCoder problems from LiveCodeBench v6
  (January to April 2025), one attempt each (three each for the two Flash-Next quants big chooses between), same
  settings. Public, so possibly seen in training; it ranks the arms
  only where the held-out set cannot, and the Qwen3.8 models it compares share their training data.
- **Swarm** (`bench-load.py load --think off`, then `scout.py`): agents at once with a scout's turn (no thinking,
  answers up to 800 tokens), up to the level where the median agent falls under 10 tok/s. The sweeps before 10-19
  ran at 16k, 32k and 64k caps but started every agent fresh, so no conversation got past about 7-11k in the 2-minute
  window whatever the cap said (the three caps matched within a few tok/s): they measure short conversations. Every
  swarm arm was swept again with 64k conversations started partway (`--pregrow`), and the agent counts below come
  from those. Then real swarms of 16, 32, 64 and 100 scouts (read-only tools: list, read lines, search; report,
  finish) over a copy of the owner's code (44,193 lines in 144 files) with 60 one-token bugs planted by `plant.py`,
  graded against the planted list: a bug counts as found when a report names its file within 3 lines of it. The
  first run, the battery (32k each, 64k once), showed it measuring the context cap at 16 and 32 scouts: each scout
  keeps every file it reads in its conversation, and 14 of the 4B's 16 scouts filled 32k and stopped with half their
  area unread (2 of 60 found). The A3B showed a second way to lose to the cap: it asks for five to ten reads in one
  turn and reports only once its whole area is read, so at 16 scouts all 16 went past 32k within 3 to 7 turns (the
  cap was checked between turns, and one batch of reads overshot it) and reported nothing, with a median 83% of
  their areas read. So every swarm model also runs a fixed grid at 64k, the owner's floor (scouts sample at
  temperature 0.7): 100 and 32 scouts three times each with rolling context (only the last 2 file reads the scout
  has seen stay in its context; its reports stand), 100 once without it, and 32 once more with thinking on (the
  battery's one thinking run was itself held by the cap), all under the scout's rules 2: the brief says how much
  context there is and to report as you go, a read that would overfill the context is refused (the next prompt is
  estimated from the server's own token counts), and a read stops where the 9,000-character cut on a tool result
  would, its header saying so. Under rules 1, the battery's, 150 lines of a file with long lines passed that cut,
  which hid about 4% of the target's lines behind a header that claimed them, so the battery's coverage figures run
  up to 4% high. The grid's runs are named `-v2` and never pass for repeats of the battery's. Rolling had its own
  hole, caught by a stub that reads in batches before any run used it: it dropped all but the last 2 reads of a turn
  before the model had seen them, which found 2 of 5 stub bugs while counting the whole area as read. Each grade
  says how many of the reported lines would land that near a planted bug at random, which planted bugs some scout
  read at all, and how much of its own area each scout read, and flags CAP-BOUND (most scouts ended at the cap,
  whatever they read) and FLOOR runs. The 27B ran the battery as a swarm of 16, 32 and 64 at 32k; its 64 took 177
  minutes (past its KV pool every turn re-reads the prefixes evicted since the last, about 600 s a turn), so its
  battery runs of 100 and with thinking were skipped (`skip-tp2b.sh`). The grid's 100 at once turned out to measure
  that same eviction on every model (Swarm, below), so from 10-22 every swarm model runs the 100 areas with only as
  many scouts at once as its pool holds (`pool_fit` in `bench-phase.sh`, from the engine's own pool size: 85% of it
  at 40k a scout; the scouts take areas off a queue), three times, once with thinking, and the 4-bit A3B once with
  200 smaller areas. The 27B does the same in batch mode, and on main's setup runs as 8 large scouts (96k each, three
  times with and without thinking), as 16 areas taken 8 at a time, and as a 100-area swarm, the owner's alternative
  to a hundred small ones. The 5060 Ti runs a swarm of its own (the 4B, and the 9B in 8 bits), also at pool_fit.
  Last, the 27B checks every distinct
  reported line against the 50 lines around it (`scout.py verify`, thinking on), the second tier the service would
  put between a swarm and its caller; graded the same way, it shows how many planted bugs survive the check and how
  many other reports it drops. Within 3 lines is loose for a noisy model: 3 of the 4B's 21 hits at 100 scouts were
  reports of something else that sat near a planted bug. So the 27B also sees each planted change (before and after)
  with each report near it and says whether the report names it (`scout.py match`), and every grade gives the
  planted bugs found that way beside the near ones. Reports away from every planted bug are not all false: the
  owner's code has bugs of its own, and a hand check of samples (below) says how many. The tensor-parallel swarm
  arms run at 90% of each card's memory (92% ran the 9B out of memory in vLLM's sampler warm-up), the 4B's two copies
  at 92%.
- **Power and heat** (`hw-window.py`): the thermal guard's 15-second samples over each arm's window.

**A test every model passes ranks nothing.** The first long-context test, the lookups, came out 12 of 12 for all nine
arms at every depth, the 2-bit model and the production 27B included. It is kept as a floor (no arm loses the thread
at 191k), and the whole-prompt questions were built to separate the arms. `longctx.py report` now prints SATURATED,
FLOOR or NO SPREAD for any depth where the arms' scores sit above 0.9, below 0.1 or within 0.1 of each other, and the
swarm models answer the same questions at 31k and 54k as a weak reference. The coding grade's first run on the box
was a floor of the other kind: every attempt failed with `sudo: user 'agent' not found` (the grader runs answers as
the harness VM's `agent` account, which the box does not have); the grader now runs them as `nobody` there, stops on
any sudo error, and the saved answers were regraded. Once graded, the held-out set did not saturate (no arm above
90%), but it could not separate the two main candidates: the 8-bit 27B passed 34 of 42, IQ3_S 32, and both passed the
same 9 of the 14 tasks on every attempt, so only 5 tasks carried any signal. The hard LiveCodeBench problems were
added for that, with IQ2_XS as the weak reference.

## Results

### Speed: one model on both 3090s (main)

| Arm | Decode, short | Decode, 50k | Decode, 175k | Cold prefill, 50k | Cold prefill, 175k | Cached first token |
|---|---|---|---|---|---|---|
| fp8-27b | 47 tok/s | 45 | 40 | 38.6 s | 186 s | 0.2 s / 1.9 s |
| fp8-27b-mtp | 67-76 | 68-95 | 77-82 | 39.5 s | 192 s | 2.2 s / 3.8 s |
| fn-iq2xs-2 | 135-147 | 121-126 | 107-118 | 13 s | 42 s | 0.06 s |
| fn-iq3s-2 | 96-102 | 87-89 | 85-93 | 22 s | 73 s | 0.08 s |
| fn-iq3s-2 (cold rerun) | 89-110 | 81-90 | 80-81 | 21-23 s | 73-74 s | 0.06-0.09 s |
| fn-q4kxl-2 (warm cache) | 21-27 | 20-46 | 22-26 | 125-133 s | 408-422 s | 0.8-1.3 s |
| fn-q4kxl-2 (cold rerun) | 12.5-12.9 | 11-11.3 | 13.5-14.1 | 197-204 s | 614-622 s | 1.5-1.7 s |
| fn-q4kxl-2t | 17-18 | 17-21 | 13-22 | 174 s | 541-548 s | 1.1-1.5 s |
| fn-q4kxl-2h | 22-28 | 35-54 | 22-50 | 170-172 s | 533-535 s | 0.6-1.0 s |
| fn-iq3s-2-p2 | 64-83 | 70-72 | 59-71 | 73-75 s | 221-234 s | 0.5-0.7 s |
| fn-iq3s-2h (hand split, 5060 Ti helper) | 120-122 | 103-108 | 87-99 | 14-26 s | 49 s | 0.06-0.09 s |
| fp8-27b-pp (pipeline, two cards, 131k) | 30 | 28 | (131k max) | 27 s | - | 0.3-0.6 s |
| fp8-27b-pp3 (pipeline, three cards) | 25 | 24 | 21 | 23 s | 118 s | 0.3 s / 3.1 s |
| w4-27b-tp2 | 119-127 | 98-99 | 84-103 | 39.0 s | 187.6 s | 1.3 s / 2.1 s |
| w4-27b-x2 (one server's) | 107-108 | 86-94 | (150k max) | 45.0 s | - | 1.6 s |
| fn-iq3s-2s | 118-121 | 105-110 | 98-115 | 13.5-14.3 s | 45.6-45.9 s | 0.06-0.08 s |
| fn-iq3s-2t | 118-124 | 100-111 | 95-96 | 13.0-14.0 s | 44.4-44.7 s | 0.06-0.09 s |
| _production 27B, 4-bit, one card_ | _97_ | _81_ | _(150k max)_ | _50 s_ | _-_ | _1.9 s_ |

**Strata's automatic split left a fifth of IQ3_S's speed on the table.** Set by hand at layer 27 with each card loading
only its own layers' dense weights (-2s), IQ3_S decodes 20% faster alone (118-121 tok/s against 96-102) and reads a
cold prompt 35-38% faster (175k in 46 s against 73 s), with the same weights, so the same answers' quality. The
automatic split prices each card's speed and link; on the batch variant's start it picked layer 20, calling 20 and 23
a near tie it cannot rank, and it does not price the prompt at all. Layer 27 is what Strata's docs report as best for
two 3090s on x16 and x4 links (#2418). The overlap tuning on top (-2t) adds about 5% at most, inside the
run-to-run spread. IQ3_S on the hand split now decodes as fast as the 4-bit 27B with MTP and reads long prompts four
times faster (175k: 46 s against 188 s).

**The 5060 Ti as an expert store doubles Q4's decode at no cost to prefill.** Both runs use the same hand split. With
the 5060 Ti holding experts neither stage holds (-2h), UD-Q4_K_XL decodes 35-54 tok/s at 50k against 17-21 without
it (-2t), and 22-50 against 13-22 at 175k. Both read a cold 50k prompt in 170-174 s. The engine logs show where the
gain comes from. Without the helper, the 3090s' caches hold 96-98% of the experts each token needs, and the CPU
computes the rest from RAM or the disk. With it, the helper computes about 11.5% of the routed experts itself,
sending only activations over its x1 link, and the share left to the CPU halves, from about 3.5% to under 2%. Prefill
does not move: a long prompt touches nearly every expert, and those still stream from the NVMe drive (one 50k
prompt made the OS read 247 GB from it). This is the owner's tiering idea: the most-used experts in the 3090s' VRAM,
the next tier on the 5060 Ti, then RAM, then disk. Strata has it built in. For IQ3_S the helper adds nothing:
with the cards in the planned order (the engine log shows the split at 27 and 5,755 more experts on the 5060 Ti) it
decodes 120-122 tok/s short and 103-108 at 50k, the hand split's own speed, and reads 175k in 49 s against 46. On
two cards IQ3_S already keeps 77% of its experts resident, so there was little left for the helper to take.

**Q4's speed depends on what the page cache holds, and the hand split helps it too.** The first default-split runs
(fn-iq3s-2, fn-q4kxl-2) came after other arms, with whatever those had left in the page cache, while every variant
started from an empty one, and against them the hand split looked slower for Q4 (17-21 tok/s against 21-27). Rerun
from an empty page cache, the default split decodes Q4 at 12.5-14 tok/s and reads 50k in 197-204 s, so the hand split
is about 40% faster (17-21) and reads 50k in 174 s. IQ3_S's default barely moved when rerun cold (89-110 against
96-102): its experts sit in VRAM, not in the page cache. A Q4 number is only comparable with one taken from the same
page cache state, and none of them is what real work sees (below).

### Speed: all three cards (big)

| Arm | Decode, short | Decode, 50k | Decode, 175k | Cold prefill, 50k | Cold prefill, 175k |
|---|---|---|---|---|---|
| fn-iq3s-3 | 93-104 | 85-87 | 90-92 | 26-27 s | 89 s |
| fn-q4kxl-3 | 35-36 | 22-26 | 30-32 | 132-142 s | 435-441 s |

**Q4's speed test flatters it: on real work it decoded 13 tok/s, and the configuration was the cause.** The speed
test sends the same few prompts again and again, so the experts they use stay in the page cache. The coding run's
answers (about 17k tokens each, thinking, a different task each time) decoded at a median 13.4 tok/s (9.8-17.5),
against 35 in the speed test. Every other arm held its speed-test rate on the same coding run (IQ3_S 94, the 4-bit
27B 84, IQ2_XS 123). During a Q4 answer the box read 270-340 MB/s from the NVMe drive, and the cards sat mostly idle
(3090 21%, 5060 Ti 0%, 3090 Ti 80%). The engine's own counters put it at about 95 GB read from the drive per answer,
25 TB over the run. A first reading blamed the 32 GB of RAM; the engine's tier log says otherwise. Strata's setup
gave the RAM copy 8 GiB of the 27.5 GiB free (its rule for a 32 GB desktop leaves 20-24 GB for a browser), and its file
tier reads the rest unbuffered, so the page cache could not help either. IQ3_S (78 GB) keeps 77% of its experts in the
3090s' VRAM and pins the rest, and it reads about 0.1 GB per request. The next section moves Q4 off the drive.

### Q4 off the drive (big)

The owner's goal (10-20): "try to not use any of the nvme at all." Every run here uses the helper split: layer 27 on
the 3090s, the 5060 Ti holding experts neither stage holds. Each run answers the same 4 held-out tasks (2 algorithmic, 2
practical, about 17k tokens each) one at a time, from an empty page cache, followed (from the -2z runs on) by one cold
50k prompt. The drive column is the drive's own read counter over the 4 answers. Same weights on every row, so the
same quality; the pass counts differ only by sampling.

| Arm | Change from the row above | Decode on the answers (median) | Passed | Drive read, 4 answers | RAM copy | Still read from the drive |
|---|---|---|---|---|---|---|
| fn-q4kxl-2h | helper split, setup's RAM budget | 23-32 tok/s (29) | 3/4 | 969 GB | 8 GiB | 15.5 GiB of experts, 2,373 lent slots |
| fn-q4kxl-2hr | pin up to 23 GiB, VRAM reserve 832 MiB | 43-57 (53) | 4/4 | 603 GB | 20.96 GiB | 2.8 GiB, 2,376 lent slots |
| fn-q4kxl-2hrp | none in effect (see below): a repeat of -2hr | 46-62 (51) | 3/4 | 593 GB | 20.97 GiB | 2.8 GiB, 2,376 lent slots |
| fn-q4kxl-2hrpk | 131k context instead of 262k | 56-68 (62) | 4/4 | 338 GB | 20.93 GiB | 0.85 GiB, 2,372 lent slots |
| fn-q4kxl-2z-kd | -2hrpk, and no expert held on two cards | 50-68 (60) | 3/4 | 425 GB | 20.94 GiB | 0.85 GiB, 2,372 lent slots |
| fn-q4kxl-2z-qd | -kd at 64k (the owner's floor) | 62-69 (65) | 4/4 | 266 GB | 20.90 GiB | none assigned; 2,345 lent slots |
| fn-q4kxl-2z-kdp | -kd with 4,096-token prompt chunks | 49-68 (61) | 4/4 | 425 GB | 20.91 GiB | 0.85 GiB, 1,350 lent slots |
| _fn-iq3s-2h (IQ3_S on the same split, for reference)_ | _the 3-bit file_ | _97-106 (102)_ | _4/4_ | _16 GB_ | _6.81 GiB, everything outside VRAM_ | _none assigned; the 16 GB is the 3090s taking over experts the 5060 Ti holds (next section)_ |

One cold 50k prompt after the answers took 103 s on -kd and 102 s on -qd (the helper split with setup's RAM budget:
170-172 s), and 169 s with the smaller chunks.

**Pinning the RAM copy nearly doubled Q4's decode on real answers.** Asked for 23 GiB, the engine's memory guard
granted 20.96: it found 25.2 GiB available at that point and keeps 4 GiB free plus a 0.25 GiB margin. That holds 7,172
of the 23.7 GiB of experts outside VRAM, against 2,738 before. The answers decoded at a median 53 tok/s against 29,
and the drive read 603 GB against 969. The rest still comes off the drive. That is 2.8 GiB of the least-used
experts (ranked by Strata's shipped profile, which was built from a document summary, not code), plus the 2,376
cache slots the prompt path borrows, none of which has a RAM copy. The VRAM tier served 97% of the expert lookups,
RAM 2.2%, and the drive 0.8% (over all 9 requests). At 0.8% the drive still read about 100 GB per answer.

**131k instead of 262k gave the experts 2 GiB of VRAM and cut the drive's reads by 44%.** The 3090s' caches grew
from 5,895 and 5,464 slots to 6,201 and 5,820, which leaves 21.8 GiB of experts outside VRAM. The RAM copy now
holds all but 0.85 GiB of them. The answers decoded at a median 62 tok/s against 51-53, and the drive read 338 GB
against 593-603. The repeat of -2hr (-2hrp) measures the noise: 51 tok/s against 53, 593 GB against 603. Still, each
answer read 60-85 GB, more than a 0.85 GiB tail explains. The -2z runs below test the two likely sources: the lent
slots, and experts the 5060 Ti holds being copied into the 3090s.

**The 4 GiB the guard keeps is not spare.** Once serving, the engine grows by about 5 GiB beyond what it had at the
check. Most of that is pinned staging and shared memory, and the kernel moved its start-up heap (2.8 GB, never touched
again) to swap. Mid-answer, the box had 0.2 GiB available. Lowering the headroom (`STRATA_RESIDENT_HEADROOM_GIB`)
would push pages the engine uses into swap. So the remaining 2.8 GiB has to come out of VRAM instead. A shorter
context gives the KV cache's VRAM to experts. `STRATA_DISJOINT_ADAPT=1` stops a 3090 from promoting an expert the
5060 Ti already holds. Such an expert is in neither the RAM copy nor the stage's cache, so each promotion is a read
from the drive and a duplicate slot. Strata's docs counted about 2,000 such duplicates under sustained use.

**At 64k every expert has a place off the drive, and the drive is still read.** At 64k the 3090s' caches grow by
330 slots, and the RAM copy holds every expert that neither the 3090s nor the 5060 Ti holds (20.82 GiB, under the 20.90
the guard allows). The placement the owner asked for is complete there: the most-used experts on the 3090s, the next
on the 5060 Ti, the rest in RAM, none assigned to the drive. The answers decoded at a median 65 tok/s, Q4's best, and
the drive read 266 GB, 66 GB per answer. The reads come from experts that leave VRAM while the engine runs: the ones
the 3090 Ti's cache and the 5060 Ti hold have no RAM copy (11,096 experts "on the mmap fallback", by the engine's
log), so one that loses its slot comes back from the drive. Neither suspected source is the main one. Halving the
lent slots (-kdp) left the reads where they were (425 GB, as -kd) and slowed a cold 50k prompt (169 s against 103).
Keeping the 5060 Ti's experts out of the 3090s' promotions made the reads worse: 425 GB against -2hrpk's 338 at the
same 131k. Which run-time move it is, the logs do not say, and the RAM for more copies is not there (0.2 GiB available
mid-answer). So Q4 tops out at about 65 tok/s on this box, against IQ3_S's 97-120 with nothing read, and the coding
tests found no quality gap to pay for it (Coding, harder). A coding order cannot move anything off the drive at 64k,
since nothing is assigned there; it is tested on IQ3_S instead (next section), where it can only change what VRAM holds.

**`--prefill 2048` never reached the engine.** Strata's server runs `setup.upgrade_config` at every start, and that
rewrites exactly "2048" to "auto": configs from before v0.1.13 used 2048, and the engine now picks its own chunk. So
-2hrp ran as -2hr again, which makes it a repeat run that measures the noise. -2hrpk ran as -2hr at 131k. The -2z
runs use 4,096 for the smaller-chunk test, a value the rewrite leaves alone. Not `--no-prefill-borrow`: under a layer
split it makes each stage withhold about 1.9 GiB of VRAM for the whole session.

### IQ3_S with a coding order (big)

The owner's tiering question (10-20) was to put the coding experts on the 3090s, the next on the 5060 Ti, the rest
in RAM. Strata places experts by a ranking learned from use, and the ranking it ships was built from a document summary,
not code. So IQ3_S learned its own from 32 prompts (12 medium LiveCodeBench problems, 12 coding tasks from Strata's
benchmark and 8 practical tasks, all distinct from the 4 below), saved to `expert-profile-code-iq3s.bin`. It differs a
lot from the shipped one: of the 2,000 most-used experts, 26% are on both lists; of the top 10,000, 62%; of the top
20,000, 85%. The two 3090s hold the top 18,821 of 24,576. Each run answers the 4 held-out tasks of the section above,
with the hand split at 262k. The runs alternated, in the table's order, so drift over the night hits both orders.

| Arm | Order | Cards | Decode on the answers (median) | Passed | Drive read, 4 answers | Cold 50k |
|---|---|---|---|---|---|---|
| fn-iq3s-2z-1 | built-in | 3090 + 3090 Ti | 104-109 (105.1) | 2/4 | 3 GB | 14.6 s |
| fn-iq3s-2z-c1 | coding | 3090 + 3090 Ti | 104-109 (107.5) | 3/4 | 3 GB | 14.7 s |
| fn-iq3s-2z-h1 | built-in | 5060 Ti as helper | 97-106 (100.0) | 4/4 | 16 GB | 15.5 s |
| fn-iq3s-2z-hc1 | coding | 5060 Ti as helper | 105-107 (106.0) | 4/4 | 11 GB | 18.6 s |
| fn-iq3s-2z-2 | built-in | 3090 + 3090 Ti | 104-107 (105.7) | 4/4 | 3 GB | 14.7 s |
| fn-iq3s-2z-c2 | coding | 3090 + 3090 Ti | 105-110 (107.6) | 3/4 | 3 GB | 14.8 s |

**On IQ3_S the tiering is already in place, and a coding order adds about 2%, not proven.** On the two 3090s the
coding order decoded at a median 107.5 and 107.6 tok/s against 105.1 and 105.7. Answer by answer (same task, same
round), it was faster on 6 of 8, a split chance gives about three times in ten (sign test p = 0.29). There is nothing
left for it to take off the drive: the RAM copy holds every expert outside VRAM (17.73 GiB), the cards exchange
experts with it (about 70,000 swaps per run, none read from the file), and the drive read under 1 GB per answer. The
speed is the cards' own decode, and a different order only changes which experts start where.

**With the 5060 Ti as a helper, the coding order only brings IQ3_S back to the two cards' speed.** The built-in order
on the helper split decoded at 100.0 tok/s here and 101.8 on 10-20 (fn-iq3s-2h, the same settings); the coding order
106.0, faster on all 4 answers against both runs, with 11 GB read from the drive against 16. That matches the two
cards alone, which read less: with the helper every expert has a VRAM place (18,821 on the 3090s, 5,755 on the 5060
Ti), so the RAM copy shrinks to the slots lent to prompts (6.81 GiB), and a 3090 taking over an expert the 5060 Ti
holds reads it from the file (81,137 such reads on -h1 and 45,804 on -hc1, none on the two-card runs). A better order means fewer of
those moves, which is the gain. Its one cold 50k prompt took 18.6 s against 14.6-15.5.

The pass counts vary by sampling alone: the same settings passed 2 of 4 (-1) and 4 of 4 (-2). Placement does not change
the weights, so the 4 answers are a check that the engine answers sensibly, not a quality measure (the coding sets do
that). So big keeps Strata's built-in order on the two 3090s: a coding order is at most 2% here and would need
relearning for each quant and engine version, and the 5060 Ti stays free.

### Agents at once

Total tok/s generated in the 3-minute window, and in brackets the median agent's own decode speed while its request
streams. Each agent works as described under Method: 40k conversations, starting partway in.

| Arm | 1 agent | 3 | 6 | 8 | 12 | 16 |
|---|---|---|---|---|---|---|
| w4-27b-tp2 (one copy, MTP) | 100 (104) | 209 (72) | 298 (50) | **342 (43)** | 244 (24) | 252 (16) |
| w4-27b-x2 (a copy per card, MTP) | 89 (93) | 248 (87) | **290 (69)** | 141 (20) | 135 (20) | 134 (22) |
| w4-27b-tp2n (one copy, no MTP) | 70 (70) | 159 (53) | 241 (40) | 311 (40) | 367 (31) | 447 (28) |
| w4-27b-tp2m1 (one copy, 1 draft) | 86 (89) | 188 (64) | 303 (52) | 382 (49) | 402 (34) | **490 (31)** |
| w4-27b-tp2m1r4080 (the same, a checkpoint per 4,080 tokens; 10-21) | 87 (90) | 186 (65) | 297 (53) | 374 (49) | 394 (32) | **452 (29)** |
| fp8-27b | 45 (45) | 110 (37) | 180 (30) | **233 (30)** | 82 (13) | 21 (3) |
| fp8-27b-mtp | 65 (67) | **124 (47)** | 78 (19) | 98 (20) | 77 (21) | 82 (18) |
| fp8-27b-pp (pipeline, two cards) | 28 (29) | 72 (24) | **100 (19)** | 64 (14) | 48 (10) | 62 (11) |
| fp8-27b-pp3 (pipeline, three cards) | 24 (24) | 62 (21) | 116 (19) | 142 (18) | **195 (17)** | 97 (9) |
| fn-iq3s-2s (hand split) | 90 (100) | 81 (104) | 74 (102) | | | |
| fn-iq3s-2t (hand split, tuned) | 96 (106) | 78 (103) | 79 (106) | | | |
| fn-iq3s-2-bm (two at once, MTP) | 45 (67) | 10 (9) | 1 (2) | | | |
| fn-iq3s-2-b4 (four slots, pipelined) | 92 (102) | 88 (42) | 83 (21) | 73 (18) | | |
| fn-iq3s-2 | 81 (93) | 65 (90) | 58 (91) | | | |
| fn-iq3s-2h (hand split, 5060 Ti helper) | 92 (101) | 74 (100) | 74 (103) | | | |
| fn-q4kxl-2 (cold) | 12 (12) | 7 (8) | 5 (9) | | | |
| fn-q4kxl-2h (hand split, 5060 Ti helper) | 28 (38) | 16 (34) | 16 (36) | | | |

**One copy across both cards against a copy per card (4-bit).** Up to 3 agents, a copy per card is faster (87 tok/s
each against 72): each card works alone, with no tensor-parallel traffic over the 3090 Ti's x4 link. At 6 they tie.
From 8 agents the single copy wins by 2.4x (342 against 141) on KV room: a card holding a whole copy has room for
201k tokens of cache, while the single copy holds 831k, four times one card's (each card keeps half the weights,
and half of every token's cache). Four or more 30-40k conversations per card overflow 201k. The engine log shows what overflowing costs. Cached conversations are evicted, an agent whose
turn comes back has its whole 30-40k conversation read again, and that prefill runs in 2,048-token steps of about
1.5 s each, during which every other agent on the card gets one decode step: the card's output falls to about 5
tok/s for one to two minutes, then runs at 250 tok/s for under a minute until the next one. Of the 10-second
samples with requests waiting, 60 of 95 were stalled like that; with 3 running and none waiting, none of 21 were.
A copy per card also caps each conversation at 150k tokens; the single copy serves 262k.

The single copy's own fall past 8 agents is not the KV pool: at 12 agents the pool was 59% full, nothing waited
and nothing was preempted, yet the slowest requests ran at 10 tok/s and the slowest first tokens took 44 s. The 8-bit
27B with MTP fell the same way past 8 in the first version of the test. The two -tp2 variants without MTP and with
one draft instead of three were added to find out whether the drafter's extra verify work per seat is the cause.

**It is the drafter: with one draft token instead of three, the single copy keeps scaling.** One agent decodes 89 tok/s
against 104 with three drafts, but from 6 agents up one draft is ahead (8 agents: 382 in total, 49 each, against 342
and 43) and it does not fall: 402 at 12 and 490 at 16 (31 each), with the pool 64% full and nothing waiting. Without
MTP the copy scales the same way at about 10-15% less (447 at 16). Three drafts cost a verify of four tokens per seat
per step whether or not they are accepted; vLLM's log put the one-draft run's mean acceptance at 1.7-1.8 tokens a
step. For one or two agents three drafts are fastest; for a main setup serving many agents, one draft.

**Strata's batch slots do not add agents either.** IQ3_S with four slots in pipelined groups (-b4, Strata's own setup
for 3 to 8 clients on a split) served 92 tok/s alone and 73-88 in total at 3 to 8 agents, the same total as one slot
taking turns, now split evenly (3 agents: 42 tok/s each, first tokens 14 s instead of 57). It spreads the wait; it does
not raise the total.

**The 8-bit 27B's agents run into its KV pool, and MTP makes that worse.** Without MTP it holds 8 agents at 30 tok/s
each (233 in total, the pool 80% full); at 12 the pool fills and the median agent falls to 13. With MTP one agent
decodes at 67 tok/s, but the drafter's share leaves a 278k pool: at 6 agents it was 95% full, first tokens took a
median 76 s, and the total fell to 78. The 4-bit 27B holds 8 at 43 each with its pool 36% full: halving the weights
is what buys the room. Past three agents the 8-bit serves most without MTP, and then still about two thirds of what
the 4-bit serves.

**Pipeline parallel loses on decode and wins on cold prefill.** Over two cards (-pp, 131k at most: 262k did not fit)
one agent decodes at 29 tok/s against tensor parallel's 45, and the pool fills at 6. Over three cards (-pp3) one
agent gets 24 tok/s, but the third card's memory holds 12 agents at 17 each (195 in total) before the pool fills.
Both read a cold 50k prompt faster than tensor parallel (23-27 s against 39 s; 175k in 118 s against 186 s on three
cards): a pipeline sends one stage's activations across the x4 link instead of every layer's. Neither comes near
the 4-bit 27B on two cards for agents (342 at 8).

**At 128k conversations (main, 10-21).** The owner's work runs conversations of 64k and more (10-19), so main's
candidates ran the same test with each conversation starting between a fresh prompt and 128k. Same columns as above;
"stalled" gives the median first-token wait of the requests sent in the window.

| Arm | 1 agent | 2 | 4 | 6 | 8 | 12 |
|---|---|---|---|---|---|---|
| w4-27b-tp2 (one copy, MTP) | 90 (93) | 129 (66) | **200 (50)** | 86, stalled 94 s | 11 | 7 |
| w4-27b-tp2m1 (one copy, 1 draft) | 78 (81) | 129 (66) | **201 (50)** | 94, stalled 111 s | 7 | 5 |
| w4-27b-tp2m1, second run | 78 (81) | 131 (67) | **201 (51)** | 93, stalled 108 s | 5 | 5 |
| w4-27b-tp2r4160 (MTP, a checkpoint per 4,160 tokens) | 86 (91) | 132 (67) | 194 (51) | 220 (37) | **245 (31)** | 16, stalled 85 s (pool 97% full) |
| w4-27b-tp2m1r4080 (1 draft, a checkpoint per 4,080 tokens) | 74 (81) | 127 (66) | 197 (51) | 231 (39) | **280 (35)** | 4, stalled 113 s (pool 91% full) |
| w4-27b-tp2n (one copy, no MTP) | 60 (61) | 46, stalled 48 s | 159 (40) | 186 (31) | **232 (29)** | 1 (pool 94% full) |
| w4-27b-x2 (a copy per card) | 67 (70) | **133 (69)** | 50, stalled 52 s | 23 | 40 | |
| fp8-27b-mtp | **66 (71)** | 29 (pool 98% full) | 29 | 36 | 10 | |

A copy per card holds one 128k conversation per card (201k tokens of pool each), and the 8-bit 27B with MTP holds
one (278k). The single 4-bit copy holds four at 50 tok/s each with MTP, three drafts or one alike, and one agent alone
gets 93 tok/s with three drafts against 81 with one. At 6 agents both MTP arms stalled with the pool 67-71% full, while
the copy without MTP held 8 at 29 tok/s each and stalled only at 12, with its pool full. The rows with a checkpoint
interval are the follow-up below.

A stall is a conversation read again from the start. The 27B reads a prompt at about 1,000-1,300 tokens a second on
the two 3090s (50k cold in 39 s, 175k in 188 s): it is a dense 27B, every weight used for every token (about 54 GFLOP
a token), and the cards are compute-bound. While a 100k conversation is read again, every other agent gets one decode step per 2,048
prompt tokens, and in the engine log the output falls to 5-10 tok/s for one to two minutes. So whether a returning
agent's conversation is still in the prefix cache decides main's capacity, more than the pool's size. The no-MTP copy
also stalled once, at 2 agents, so a miss is not MTP's alone.

**With a Mamba checkpoint every five blocks instead of every block, MTP holds 8 agents at 128k.** The 27B is a hybrid:
48 of its 64 layers keep a running state instead of a KV cache, so a cached conversation can only be resumed from a
point where that state was saved. With MTP, vLLM saves it at every block ("defaulting prefix_cache_retention_interval
to dense checkpointing": its sparse default never hits under MTP), each save the size of a KV page; a block is 832
tokens with three drafts and 816 with one, the size at which a page holds a state. The follow-up ran both MTP arms with
`--prefix-cache-retention-interval` at five blocks (4,160 and 4,080 tokens; vLLM refuses a value that is not a multiple
of the block), ran -tp2m1 as it was a second time, and recorded the share of each window's prompt tokens that came from
the cache. -tp2m1 stalled at 6 again, and its cache share fell from 99% at 4 agents to 7% at 6 and 4% at 8. With
sparse checkpoints the share stayed at 97-99% through 8 agents, and the one-draft copy served 280 tok/s at 8 (35 each),
ahead of the copy without MTP (232, 29 each); three drafts served 245. Neither holds 12: eight conversations filled
82-86% of the 850k pool, so twelve need about a million tokens, and the pool filled and the share fell to 5-6%. The
sparse checkpoints cost nothing measured: one agent decodes at the same speed (81 and 91 tok/s against 81 and 93),
and the median first token took 1.5-2.1 s against 1.6-1.7 s, the re-read of up to five blocks.

What the dense checkpoints do to the pool is not measured directly. The engine's pool use counts only the blocks that
running requests hold; a finished turn's cached blocks count as free until something evicts them. At the dense run's
stall the log showed use falling to 32% while turns waited and missed, and at 6 agents both runs peaked at the same
67%, one hitting 98% and the other 7%. The numbers fit the account that every cached conversation takes several
times its KV under dense checkpoints, so four fit beside the running ones and six do not; with sparse checkpoints
eight fit, and twelve do not fit even as KV.

At 40k conversations the sparse checkpoints cost a little at the top (the -tp2m1r4080 row in the first table): within
2% of -tp2m1 up to 8 agents, 394 against 402 at 12 and 452 against 490 at 16, with the median first token at 16
agents 2.2 s against 1.4 s. There 93-96% of prompt tokens came from the cache instead of 98%: a returning turn reads
up to 4,080 tokens again, a larger share of a shorter conversation, and at 16 agents those re-reads take turns from
decoding. The owner's conversations run 64k and more, where 16 agents do not fit the pool anyway (16 x 64k is over a
million tokens) and dense checkpoints stall at 6, so main runs sparse.

_First version of the test (levels ran into each other, every agent started fresh; totals only, superseded):_

| Arm | 1 agent | 3 agents | 6 agents | Each agent at 6 | First token at 6 (median) |
|---|---|---|---|---|---|
| fp8-27b | 45 | 115 | 189 | 32 tok/s | 0.7 s |
| fp8-27b-mtp | 63 | 146 | 180 | 39 tok/s (slowest 28) | 8.3 s |
| fn-iq2xs-2 | 108 | 100 | 82 | 117 tok/s, one at a time | 99 s |
| fn-iq3s-2 | 78 | 71 | 64 | one at a time | over 90 s |
| fn-iq3s-3 | 68 | 72 | 69 | 97 tok/s, one at a time | 89 s |
| fn-q4kxl-2 | 19 | 6 | 16 | one at a time | minutes |
| fn-q4kxl-3 | 16 | 15 | 16 | one at a time | minutes |
| fn-iq3s-2-p2 | 40 | 28 | 28 | two at a time | - |

vLLM batches: more agents, more total, a little less each. The 8-bit 27B's KV pool holds 331k tokens (278k with
the MTP drafter's share taken out). Strata serves one request at a time: the total stays flat or falls as agents are
added, and each new agent waits for the ones ahead of it.

**Strata decoding two at once is worse on every count,** with MTP or without. Without (-p2, the first version of the
test), the second request's working memory came out of the expert cache in VRAM (17.3 to 14.2 GiB; hits 88% to
72%), so both requests decoded slower than one did alone, and prefill slowed 3x. With MTP kept (-bm, the fixed test),
the two slots and their drafters took about 7 GiB from the two cards' expert caches, prompts were read at 200-350
tok/s instead of about 2,200 (50k cold: 85-88 s against 21-22 s), one request alone decoded at 55-78 tok/s against
96-102, and 3 agents got 10 tok/s between them. Strata's docs measured `--batch-mtp`'s gain with short greedy replies
and say it pays only when the experts do not fit in VRAM anyway; on two 3090s IQ3_S's mostly do. Strata's agents take
turns: on the hand split at 6 agents the median agent got 12-15 tok/s over the window, the least got nothing, and
first tokens took 60-110 s, while each request, once it ran, decoded at full speed. Measured again with the cut fixed, the
default split and the helper do the same: at 3 agents IQ3_S's median agent got 16 tok/s of the window. Q4's agents
mostly got nothing inside the 3-minute window (median share 0), since one answer at 12-38 tok/s outlasts it.

**The third card adds little.** IQ3_S already fits the two 3090s' expert caches well; on three cards it decodes at
the same speed and prefills 20% slower (the 5060 Ti sits on a x1 link). UD-Q4_K_XL gains decode speed (21-27 to
35 tok/s) but not prefill: with setup's 8 GiB RAM copy, most of the experts VRAM does not hold come off the NVMe
drive, and the drive sets the pace. Each new question over an already cached 175k prompt still took about 57 s on fn-q4kxl-3 (5.8 s on
IQ2_XS), because the experts that question routes to are read from disk again.

### Long context

| Arm | Lookups, 31k / 124k / 191k | Whole-prompt questions, 31k / 54k / 124k / 191k (mean score; exact) |
|---|---|---|
| fp8-27b-mtp | 12/12, 12/12, 12/12 | 0.80 (3/10) / 0.74 (9/15) / 0.79 (8/15) / 0.61 (4/15) |
| w4-27b-tp2 | not run (lookups dropped once every arm aced them) | 0.76 (3/10) / 0.76 (7/15) / 0.74 (7/15) / 0.69 (5/15) |
| fn-iq3s-2 | 12/12, 12/12, 12/12 | 0.81 (3/10) / 0.78 (9/15) / 0.83 (9/15) / 0.80 (8/15) |
| fn-iq2xs-2 | 12/12, 12/12, 12/12 | 0.86 (4/10) / 0.75 (8/15) / 0.82 (8/15) / 0.73 (5/15) |
| fn-q4kxl-3 | 12/12, 12/12, 12/12 | 0.83 (5/10) / 0.77 (10/15) / 0.85 (9/15) / 0.73 (7/15) |
| fp8-27b, fn-q4kxl-2, fn-iq3s-3, fn-iq3s-2-p2 | 12/12 at every depth | not run (same weights as an arm above) |
| production 27B | 12/12, 12/12 (150k max) | not run (w4-27b-tp2's weights; a card's copy holds 150k, so 191k does not fit) |
| a3b-fp8 (thinking off, as every row) | - | 0.50 (1/10) / 0.61 (5/15) |
| q4b-bf16-dp | - | 0.32 (1/10) / 0.46 (3/15) |
| w4-27b-tp2b (the 4-bit 27B's swarm arm) | - | 0.67 (2/10) / 0.75 (7/15) |
| a3b-awq | - | 0.56 (1/10) / 0.59 (5/15) |
| q9b-bf16 | - | 0.43 (1/10) / 0.50 (3/15) |

The whole-prompt questions separate the model families only at 191k: there the 27B fell to 0.61 (4 exact of 15),
and both Flash-Next quants held at 0.73-0.80. Most of the 27B's losses at 191k are cross-file calls: twice it named
the same unrelated function (`_human_age`) for two different questions, and once it said the function asked about
did not exist in the file named (it does; another file has one of the same name). From 31k to 124k the three arms
sit within 0.08 of each other, and the 2-bit IQ2_XS scores as well as IQ3_S: the test does not rank quants. The
4-bit 27B fits the same reading: 0.69-0.76 at every depth, level with the 8-bit up to 124k and above it at 191k
(0.69 against 0.61), which is noise between two quants of one model, not a 4-bit advantage. Of the
55 questions, 16 were answered perfectly by all three arms; three were missed by all three, and each of those was
checked against the source and is right (a name defined in two files; five config helpers that take `key`, found
at 31k and missed at 54k by every arm).

The 4-bit Flash-Next adds nothing here: 0.73-0.85 at every depth, level with IQ3_S and IQ2_XS. The test's own noise
is about that size. The 4-bit 27B ran it twice on the same weights, once as the main arm and once as the swarm arm,
and scored 0.76 and 0.67 at 31k (10 questions) and 0.76 and 0.75 at 54k, so read gaps under 0.1 as no gap. The
swarm models, asked the same questions without thinking, sit a tier down at 31k and 54k: the A3B 0.50 and 0.61, the
4B 0.32 and 0.46.

### Coding (held-out set, 14 tasks x 3 attempts)

| Arm | Passed of 42 | Per attempt | Algorithmic | Practical | Output tokens | Wall time |
|---|---|---|---|---|---|---|
| fp8-27b-mtp | 34 | 12, 12, 10 | 15/18 | 19/24 | 688k (median 17k per answer) | 66 min (3 at once) |
| w4-27b-tp2 | 30 | 10, 10, 10 | 15/18 | 15/24 | 710k (median 17k per answer) | 47 min (3 at once) |
| fn-iq3s-2 | 32 | 11, 11, 10 | 16/18 | 16/24 | 682k (median 17k per answer) | 121 min (one at a time) |
| fn-iq2xs-2 | 27 | 10, 7, 10 | 14/18 | 13/24 | 707k (median 17k per answer) | 95 min (one at a time) |
| fn-q4kxl-3 | 36 | 12, 12, 12 | 17/18 | 19/24 | 690k (median 17k per answer) | 860 min (one at a time) |
| _production 27B, 4-bit (10-06, one attempt)_ | _11 of 14_ | | _5/6_ | _6/8_ | | |
| _Flash-Next IQ2_XS (10-05, one attempt, 32k answer cap)_ | _7 of 14_ | | _4/6_ | _3/8_ | | |

**The 4-bit Flash-Next passed the most: 36 of 42,** 12 on every attempt, missing only the spreadsheet task three
times and three single attempts. Paired by task and attempt it beats IQ2_XS clearly (10 against 1, p = 0.01) and
the 4-bit 27B probably (8 against 2, p = 0.11), but not IQ3_S (6 against 2, p = 0.29) or the 8-bit 27B (4 against 2,
p = 0.69). That is too close to call a tier, so Q4 and IQ3_S both took the 24 hard problems three times each (next
section), and there they tied. Its answers cost 860 minutes one at a time against IQ3_S's 121, at the real-work speed
above.

### Coding, harder (24 hard LiveCodeBench problems)

| Arm | Passed of 24, first try | Three tries | Answers cut off at 24k | Wall time |
|---|---|---|---|---|
| fp8-27b-mtp | 14 | | 1 (re-asked at 32k: finished at 18k, still wrong) | 44 min (3 at once) |
| w4-27b-tp2 | 13 | | 1 (re-asked at 32k: finished at 19k, still wrong) | 28 min (3 at once) |
| fn-iq3s-2 | 16 | 16, 13, 15: 44 of 72 | 0 | 72 min a try (one at a time) |
| fn-q4kxl-2hrpk (Q4, its fastest placement) | 16 | 16, 14, 14: 44 of 72 | 1 (re-asked at 32k: finished at 17k, still wrong) | 118 min a try (one at a time) |
| fn-iq2xs-2 | 11 | | 2 (re-asked at 32k: one wrong, one cut off again) | 60 min (one at a time) |

The 24k answer cap was a bench slip (production allows 32k): every answer it cut off, here and in the held-out set
(one of IQ2_XS's, one of the 4-bit 27B's), was asked again at 32k with a fresh seed and regraded. None passed, so no
score above moved.

The test separates quality tiers but not the two candidates. The weak reference IQ2_XS came last (11), solved no
problem the other two missed, and missed two both of them solved. The 8-bit 27B and IQ3_S both solved the same 12
problems and both missed the same 6; the 27B alone solved 2 and IQ3_S alone 4 (one of those 4 is the problem where
the 27B's answer was cut off). A split of 2 against 4 over six problems is what chance gives. Six problems beat all
three arms and ten were solved by all three, so the test has room at both ends. Over both coding tests the 27B and
IQ3_S each passed 48 of 66 attempts (IQ3_S's first tries) and IQ2_XS 38: on coding, IQ3_S matches the 8-bit 27B, and
IQ2_XS is a tier down.

**Q4 does not code better than IQ3_S.** Both took the 24 problems three times each, with the same seeds. Each passed
44 of 72, each did better than the other on 3 problems, and every gap was one try in three. Q4 solved 18 problems at
least once and 12 on all three tries; IQ3_S 19 and 10. Over both coding sets, 38 problems and 114 tries each, Q4 passed
80 and IQ3_S 76: Q4 did better on 7 problems and IQ3_S on 3, a split a coin gives about one time in three (sign test p
= 0.34). Strata measures Q4 as agreeing with the 8-bit model's top token 93% of the time against IQ3_S's 88%; on these
problems that gap did not turn into solved problems. If Q4 is better at all, it is by a few percent, while it decodes
at about 60% of IQ3_S's speed on the same answers (62 tok/s on its fastest placement against IQ3_S's 97 on the default
split) and still reads the drive.

**The 4-bit 27B against the 8-bit (same model, production's quantization).** Over both tests 43 of 66 against 48.
Paired by task and attempt, the 8-bit alone passed 7 and the 4-bit alone 2; everything else matched. Five of the
8-bit's seven are on the held-out set's practical tasks (CSV statistics twice, INI merge, semver resolution, time-zone
buckets); on the algorithmic tasks the two are identical (15 of 18). Seven against two is not proof: a coin gives a
split that lopsided about one time in five (sign test p = 0.18), and IQ3_S against the 4-bit is 9 against 4 (p =
0.27). The 8-bit and IQ3_S, by the same count, are 6 against 6. Read it as the 4-bit 27B probably a little behind the
8-bit and IQ3_S on coding, mostly on practical tasks, and ahead of IQ2_XS (38). It also answered at the same
length (median 17k tokens), so its 2x decode speed is a 2x shorter wait for an answer.

### Work per token and per hour (10-21)

The owner's measure (10-21): "most work done per token", work being "reasonable quality that can be expected from
best use case". The local model is a tight implementation agent working from Claude's specs, so work is a
test-verified pass, and the best use is to retry against the tests until an attempt passes. "Retry" below charges a
task the tokens of its attempts up to the first that passes (all three if none does), averaged over the six orders
of its three attempts; thinking and failed attempts count. Held-out set, 14 tasks x 3 attempts; hard set, first try.

| Arm | Passes per million tokens, held-out | With retry: solved of 14 | Million tokens spent | Solved per million | Practical tasks solved with retry | Hard set, passes per million (first try) |
|---|---|---|---|---|---|---|
| fn-q4kxl-3 (Q4 Flash-Next) | 52.2 | 13 | 0.290 | 44.8 | 7 of 8 | 39.8 |
| fn-iq3s-2 (IQ3_S Flash-Next) | 46.9 | 13 | 0.314 | 41.4 | 7 of 8 | 40.2 |
| fp8-27b-mtp (8-bit 27B) | 49.4 | 12 | 0.316 | 38.0 | 7 of 8 | 34.6 |
| w4-27b-tp2 (4-bit 27B) | 42.3 | 12 | 0.355 | 33.8 | 7 of 8 | 31.4 |
| fn-iq2xs-2 (IQ2_XS Flash-Next) | 38.4 | 12 | 0.380 | 31.5 | 6 of 8 | 26.1 |

**With retries the 4-bit 27B solves what the 8-bit does, at about 12% more tokens.** Both solve 12 of the 14 tasks
and 7 of the 8 practical ones; the practical attempts the 4-bit lost (15 of 24 against 19) come back on a retry, so
its gap at best use is tokens, not tasks. Per token the Flash-Next quants lead (41-45 solved per million against the
27B's 34-38), and the three tries on the hard set agree (IQ3_S 28.1, Q4 26.0 per million, solving 19 and 18 of 24).

Work per hour is work per token times the tokens a setup delivers, each at its measured best:

| Setup | Total decode (Agents at once) | Solved per hour, held-out with retry |
|---|---|---|
| main, the 4-bit 27B with sparse checkpoints: 16 agents, 40k conversations | 452 tok/s | 55 |
| the same: 8 agents, 128k conversations | 280 tok/s | 34 |
| the 8-bit 27B without MTP: 8 agents, 40k | 233 tok/s | 32 |
| IQ3_S Flash-Next, one answer at a time (decode on real answers) | about 105 tok/s | 16 |
| Q4 Flash-Next, its fastest placement | 62 tok/s | 10 |
| the 8-bit 27B with MTP, 128k (holds one) | 66 tok/s | 9 |

The ranking turns over. Flash-Next does the most work per token and the least per hour: it serves one answer at a
time, so its 41-45 solved per million tokens come at about 105 tok/s. The 4-bit 27B does the least per token of the
three and the most per hour, 2-3.5x IQ3_S's, because one copy across both 3090s serves many agents from a large pool.
At the owner's conversation lengths (64k and more) the 8-bit 27B fits one agent with MTP, and its no-MTP run at 128k
was not measured. The sample is small: 14 tasks, one run of each load level.

### Swarm

**Agents at once with a scout's turn** (no thinking, answers up to 800 tokens, 64k conversations starting partway
in). Total tok/s, and in brackets the median agent's decode speed while it streams.

| Arm | 1 agent | 4 | 8 | 16 | 24 | 32 | 48 |
|---|---|---|---|---|---|---|---|
| q4b-bf16-dp (4B, a copy per card) | 74 (78) | 214 (64) | 350 (52) | 548 (40) | **575 (29)** | 208 (7) | |
| q9b-bf16 (9B) | 75 (78) | 228 (60) | 365 (47) | 503 (33) | **584 (25)** | 97, 19 waiting | 73 (3) |
| a3b-awq (A3B, 4-bit) | 140 (150) | 336 (91) | 491 (66) | 620 (40) | 697 (30) | **724 (24)** | 60 (2), pool full |
| a3b-fp8 (A3B, 8-bit) | 149 (160) | 318 (85) | **466 (61)** | 88 (8), pool full | | | |
| w4-27b-tp2b (4-bit 27B, batch mode, one copy) | 62 (69) | 163 (45) | 234 (30) | **294 (19)** | 13 (1), pool full | | |
| w4-27b-x2b (the same, a copy per card) | 46 (50) | 146 (42) | **237 (33)** | 36 (2), pools full | | | |
| q4b-5060 (4B, the 5060 Ti alone) | 44 (45) | **131 (34)** | 48 (9) | | | | |
| q9b-5060 (9B in 8 bits, the 5060 Ti alone) | **36 (37)** | 33, 4 waiting: its 88k pool holds one | | | | | |

The A3B (35B, 3B active) decodes twice as fast as the 4B and 9B for one agent and serves the most in total; its
8-bit build's 35 GB of weights leave a 576k pool, which sixteen 64k conversations fill. The 4-bit 27B in batch mode
serves at most 294 tok/s (16 agents), 40% of the 4-bit A3B's best, and its 806k pool fills at 24; with a copy per
card, the two 216k pools fill at 16. Every arm falls off a cliff
somewhere between 16 and 48 conversations of this length, far short of a hundred.

**Real swarms on the planted-bug target, the grid (rules 2, 64k a scout, temperature 0.7).** Found: planted bugs a
report names within 3 lines (of 60). Other: reports away from every planted bug (some are real bugs in the owner's
code; see below).

| Arm | Setting | Found | Other | Wall |
|---|---|---|---|---|
| q4b-bf16-dp | 100 at once, whole context | 26 | 195 | 67 min |
| | 100 at once, rolling | 17, 17, 19 | 412, 266, 332 | 48-56 min |
| | 32, rolling | 4, 14, 14 | 122, 116, 142 | 15 min |
| | 32, rolling, thinking | 1 (31 of 32 called finish within a few turns) | 15 | 4 min |
| | 100 areas, 32 at once (pool_fit) | 25, 21 | 167, 153 | 25.7 min |
| a3b-fp8 | 100 at once, whole context | **42** | 45 | 113 min |
| | 100 at once, rolling | 28, 25, 26 | 56, 42, 48 | 176-180 min |
| | 32, rolling | 21, 20, 20 | 35, 15, 27 | 56-60 min |
| | 32, rolling, thinking | 28 | 111 | 74 min |
| | 100 areas, 12 at once (pool_fit) | 41, 38 | 47, 32 | 31.3-32.8 min |
| | the same, thinking | 42 | 185 | 58.7 min |
| a3b-awq | 100 at once, rolling | 30 | 58 | 167 min |
| | 100 at once, whole context | 34 | 57 | 124 min |
| | 100 areas, 35 at once (pool_fit) | **43, 43, 39** | 51, 50, 48 | **27.5-29.9 min** |
| | the same, thinking | 41 | 167 | 39.0 min |
| | 200 areas, 35 at once | 44 | 84 | 42.6 min |
| q9b-bf16 | 100 at once, whole context | 25 | 120 | 84.5 min |
| | 100 areas, 30 at once (pool_fit) | 28, 27, 28 | 95, 87, 107 | 23.0-26.0 min |
| | the same, thinking | 25 | 119 | 15.1 min |
| w4-27b-tp2b (27B, batch mode) | 100 areas, 17 at once (pool_fit) | 10, 9, 8 | 4, 5, 9 | 68.3-69.6 min |
| | the same, thinking | 35 | 37 | 299.9 min |
| w4-27b-tp2 (27B, main's model, dense checkpoints) | 8 large scouts, 96k each | 13 | 8 | 19.5 min |
| | the same, thinking | 19 | 4 | 28.4 min |
| w4-27b-tp2m1r4080 (main as decided) | 8 large scouts, 96k each | 8, 10, 14 | 8, 1, 6 | 23.3-26.7 min |
| | the same, thinking | 21, 15, 16 | 2, 1, 4 | 29.2-30.5 min |
| | 16 areas, 8 at once, 96k each | 12 | 3 | 56.6 min |
| | the same, thinking | 28 | 13 | 60.0 min |
| | 100 areas, 16 at once | 11 | 2 | 150.1 min |
| q4b-5060 (4B, the 5060 Ti alone) | 100 areas, 6 at once (pool_fit) | 23 | 175 | 65.1 min |
| q9b-5060 (9B in 8 bits, the 5060 Ti alone) | 100 areas, 2 at once (pool_fit) | 29 | 87 | 139.8 min |
| combo-main-q4b (q4b-5060 beside main under load) | 100 areas, 6 at once (pool_fit) | 21 | 213 | 71.2 min |

**Rolling context loses on every model it ran on.** The A3B found 25-28 rolling against 42 with its whole context,
the 4B 17-19 against 26, and the 4-bit A3B 30 against 34. Rolling scouts never meet the cap, so they read on to their turn
limit: 66-78 of the A3B's 100 ended at 80 turns, against 13 at 40 with whole context, and sent twice the prompt
tokens (64-65M against 33M) for fewer finds and 3 hours instead of 2. The 4B's rolling runs were faster (48-56 minutes
against 67) and reported up to twice the noise. Whole context, the brief's "report as you go", and the refused read
at the cap make a better scout than forgetting.

**The A3B is the swarm model of the grid; the 4B is weak.** At 100 scouts the 8-bit A3B found 42 of 60 with 45
other reports; the 4B found 26 with 195. Of the distinct lines each reported, 26 of the 4B's 201 sit at a planted
bug, against 43 of the A3B's 79. A hand check of the A3B's 28 reports away from every planted bug in an earlier run
found 9 real bugs already in the owner's code, so its noise is lower still. Thinking bought the A3B finds (28 against 20-21 at 32
rolling) at four times the noise and five times the tokens; the 4B with thinking quit at once.

**At 100 at once the swarm is all prefill.** The engine log over the A3B's best run: 7% of prompt tokens came from the
prefix cache, 32.5M prompt tokens were computed at about 4,800 a second (the whole 113 minutes) to generate 0.63M, and
a scout's turn came round every 4.4 minutes (15 turns in a median 66). The rolling runs hit 1-2%. The cache should
hold: without MTP, vLLM keeps each conversation's state at the last block boundary of its last prompt, which the next
turn extends. What breaks it is churn. Each miss computes a whole conversation into new blocks; at 4,800 tokens a
second that pushes the 576k-token pool through its LRU in about 2 minutes, sooner than the scout comes back, so its
next turn misses too. The engine's own gauge hides it: on the 4-bit build's run, 78 requests waited "for capacity"
with KV use at 19%, since that figure leaves out cached blocks no running request holds. The 27B's 8 large scouts are
the counter-case: 8 x 91k fit its 831k pool, and they made 33 turns each in 18 minutes, which the 27B's prefill
(about 1,100 tokens a second) could not have done reading every turn again (about 12M tokens, three hours). The grid's 100 against 32 also changed two things at once, each scout's
area (442 lines against 1,380) and how many ran at once. So every swarm model runs the same 100 areas again with
only as many scouts at once as 85% of its pool holds at 40k each (about the 90th percentile of a 100-area scout's
final context): `bench-phase.sh`'s `pool_fit`, read from the engine's metrics. On the 4-bit A3B that is 35 at once,
and the first run took 27.5 minutes against 124 with all 100 at once, with 87% of prompt tokens from the cache ten
minutes in (5% over the 100-at-once run), nothing waiting, and a median scout done in 8 minutes instead of 103. Its
three runs found 43, 43 and 39 against the 100-at-once run's 34, from the same areas read as fully (a median 100%
each); one slow run below all three is a hint, not a measured effect. Thinking found no more (41) with three times
the noise and 2.5 times the tokens. Halving the areas (200 areas of 221 lines) found 44 with 84 other reports in 43
minutes: one or two more bugs for two thirds more noise and half again the time, so 100 areas stay. The 9B gains
the same way, 3.5x faster (23-26 minutes at 30 at once against 84.5 with all 100) and a few more finds (27-28 against
25), but stays a tier below the A3B: two thirds of its finds with twice its noise. Thinking made its scouts quit
early (97 of 100 called finish) and found 25. The 4B at 32 at once finished in 26 minutes against 67 but found no
more (25 and 21 against 26), with 153-167 other reports: its limit is the model, not the cache. The 8-bit A3B's 576k
pool holds 12 at once, and it finished in 31-33 minutes against 113 with all 100, finding 41 and 38 (42 at 100 at
once) with 32-47 other reports. Against the 4-bit build at 35 at once (39-43 found, 48-51 other, 28-30 minutes) that
is the same work, with a little less noise and three times less room: the 4-bit A3B is the swarm, and its 1.66M
pool is what lets a larger target or two passes at once fit. Thinking on the 8-bit build found 42 with four times
the noise, three times the tokens and twice the time, as on the 4-bit.

**The 27B is not a swarm model, and without thinking it barely reports.** In batch mode at 17 at once it found 8-10
in 68-70 minutes, with 4-9 other reports. Its scouts read every line of their areas, but 73-80 of 100 ran out their
40 turns and 84 reported nothing: about 72 tokens a turn, tool calls with no comment, and a median 40k of context by
the end, against the A3B's 22 turns and 1.2 reports a scout. It reads first and reports at the end, as it did in the
battery, and the end does not come within 40 turns. With thinking it reports as it reads (99 of 100 called finish,
in a median 18 turns) and found 35 with 37 other reports, but in 300 minutes and 2.4M tokens, ten times the A3B's
time for fewer finds. Batch mode runs int8 activations, and main's own build on the same 100 areas found 11 with 2
other reports, in the same way (80 of 100 scouts out of turns, 88 with nothing reported) and in 150 minutes: the int8
activations cost nothing measured, and batch mode does this job in half the time.

**Main doing a swarm's job: few large scouts, thinking on, about 28 in an hour with little noise.** Eight scouts of
about 5,500 lines each at 96k read 73-79% of their areas before the cap and found 8-14 without thinking and 15-21
with it, 1-8 other reports each run (the dense-checkpoint build matched: 13 and 19). Sixteen areas of about 2,760
lines, taken 8 at a time, were read in full: 12 found without thinking and 28 with it, 13 other reports, in an
hour. That is the most precise swarm setting measured, but the A3B finds half again as many in half the time, and
some of its extra reports are real bugs. Main is the setting for when the swarm setup is not loaded and a switch is
not worth it.

**The 5060 Ti alone is too small to swarm well.** The 4B there serves 131 tok/s at 4 agents of 64k, and its 314k pool
holds 6 scouts at once: 23 found with 175 other reports in 65 minutes, the 4B's quality at two and a half times its
time on the 3090s. The 9B in 8 bits leaves an 88k pool, one 64k conversation at a time, so its swarm ran 2 at once
and took 140 minutes for 29 found with 87 other, the 9B's quality at five times its time. Its use is beside main,
on work that does not need the 3090s (main and swarm at once, below).

**A second copy finds more than a smaller area or a smaller model, and agreement ranks the noise.** Each 4-bit A3B
run at 35 at once is an independent pass over the same areas. 34 planted bugs were found by all three, and 15 more
by one or two: two passes found 46-47 between them, three 49, all six of its runs 53, and every run of every model
54, so 6 were never found. A second pass adds about 4 for about 29 minutes, against 200 areas' one or two for 43. The
9B's passes vary more (17 in all three, 37 in any) and find 4 bugs the A3B's three missed, so a different model of
lower quality adds about as much as a third copy of the better one. Among the A3B's other reports, 11 spots were
flagged by all three passes and 80 by only one; 5 of the 11 are bugs the hand check confirmed real, against 1 of
the 80. That check covered 28 reports of an earlier run, so it leans toward what the A3B flags often and overstates
the gap; the 27B's check of every report (pending) measures it properly.

**Main and the 5060 Ti's swarm at once cost main nothing.** With main (w4-27b-tp2m1r4080) serving 8 agents of 128k
on the 3090s and the 4B swarming on the 5060 Ti (combo-main-q4b), main served 291-294 tok/s (37 each) in seven
10-minute windows back to back while the swarm ran, against 280 (35 each) alone, with 98-99% of prompt tokens from
the cache and first tokens in 1.5 s. The swarm beside it found 21 with 213 other reports in 71 minutes, against 23 and
175 in 65 alone: about 9% slower, and finds within the 4B's spread (21-25). (The combo's first main window, 4.7
tok/s, is the 8 conversations still being grown to 128k when the 300-second warm-up ran out, not a measurement.) So
the 5060 Ti can swarm beside a busy main for free, but only at the 4B's quality: a wide, noisy first pass.

**The 27B's check, as first written, drops a third of the real bugs and most of the real ones nobody planted.**
Main's 27B, thinking on, checked every distinct line any scout run reported (2,423 lines, each against the 51 lines
around it, with the first report's wording), and only what it called a bug was kept. Across the 68 runs it kept 69%
of the planted bugs found and 3% of the other reports: the A3B's best run went from 41 found with 44 other to 27
with 4. Of the 57 planted bugs some run named, it kept 31 and never kept 26. It was right to drop 5 of those: three
turn `<` into `<=` at a float threshold or a tie, which no realistic input tells apart, and in two an `and`/`or` swap
is corrected by a later line. The bench's own shortcut lost 3: verdicts are cached by line, so a wrong report judged
first at a line decided for the right ones after it. Three needed code outside the window (a line the report cites,
another file). The other 15 it got wrong with the code in view: an `or` made `and` so a fallback lookup crashes or
always misses, a health check that returns True when the check fails, a condition inverted under its own error
message, each dismissed as "a guess about intent". The prompt invited that: "guesses about intent are not bugs" was written to cut noise, and the 27B read
logic bugs as guesses. Off the answer key it is the same: of the reports the hand checks examined, it kept 2 of the
11 real bugs and none of the 23 false reports. As a filter it is precise and blind to the bugs that matter. The
service keeps every report and only labels it with the verdict, but a "not a bug" on a third of the real ones
misleads whoever reads it.

_Pending: the check again, the same model and lines, with a prompt that judges a line against what its own code says
it should do (names, comments, messages, the lines beside it), and a second arm that also shows the lines a report
cites._

### Power and heat

Means over the minutes a card was busy (utilization over 50%); no card logged a thermal slowdown sample in any arm.

| Arm | 3090 | 3090 Ti | 5060 Ti | CPU peak |
|---|---|---|---|---|
| fp8-27b, fp8-27b-mtp (speed) | 270-272 W, 65 C | 269-270 W, 60 C | idle | 93-96 C |
| fp8-27b-mtp (74 min of coding) | 274 W, 65 C | 273 W, 60 C | idle | 98 C |
| fn-iq2xs-2 | 238 W, 6 busy min | 231 W, 16 busy min | idle | 71 C |
| fn-iq3s-2 | 202 W, 4 busy min | 234 W, 23 busy min | idle | 80 C |
| fn-q4kxl-2 | 150 W, 6 busy min | 149 W, 30 busy min | idle | 78 C |
| fn-iq3s-3 | 213 W, 4 busy min | 219 W, 23 busy min | 41 W | 74 C |
| fn-q4kxl-3 | 146 W, 2 busy min | 153 W, 29 busy min | 28 W | 81 C |

vLLM keeps both 3090s at their 275 W limit for as long as it has work, and the CPU near its 100 C limit (the
engine's busy loops, not the model). Strata leans on the 3090 Ti (which holds the last layers and most of the
expert work), draws less, and stays cooler on the CPU; the slower its quant, the lower the watts, because the cards
wait on the drive.

### Switch time

From `docker run` to the first token. "Warm" means the model files were in the page cache and the engine's
compiled kernels in its cache volume, as when the scheduler switches back to a setup it ran recently.

| Arm | Switch |
|---|---|
| fp8-27b | 213 s |
| fp8-27b-mtp | 182 s; 112 s warm |
| fn-iq2xs-2 | 536 s first start (prepares its pack), 82 s warm |
| fn-iq3s-2 | 92 s |
| fn-q4kxl-2 | 44 s warm |
| fn-iq3s-3 | 133 s |
| fn-q4kxl-3 | 173 s (includes a one-time kernel build for the 5060 Ti); 162 s later |
| fn-iq3s-2s, fn-iq3s-2t (hand split) | 93-104 s |
| fn-iq3s-2h (hand split, 5060 Ti helper) | 133 s (three starts) |
| fn-q4kxl-2h | 136-170 s |
| fn-q4kxl-2hr, -2hrp, -2hrpk (23 GiB pinned) | 92-96 s |
| w4-27b-tp2 | 152 s first start, 50-71 s after |
| w4-27b-tp2n, -tp2m1 | 121 s, 132 s |
| w4-27b-x2 (two servers) | 131 s |
| fp8-27b-pp, -pp3 | 172 s; 122-222 s |
| swarm: w4-27b-tp2b, -x2b | 182 s each |
| swarm: a3b-awq, a3b-fp8 | 202 s, 171 s |
| swarm: q9b-bf16, q4b-bf16-dp | 112 s, 111 s |
| swarm on the 5060 Ti: q4b-5060 | 151 s |

Every setup is up within about three and a half minutes, most within two. The scheduler should still batch work by
setup: the slowest switch costs about as long as two of IQ3_S's coding answers. Times are to the nearest 10 seconds
(the health check polls). A switch also pays for stopping the arm before it, which is not in these times.

## Reading

**Main is the box's workhorse, and the 4-bit 27B on both 3090s is it.** Every candidate for main runs the same model
family, and the coding tests do not separate them by much: the 8-bit 27B and IQ3_S each passed 48 of 66 attempts,
the 4-bit 27B 43, and with retries against the tests all three solve 12 or 13 of the 14 held-out tasks. What separates
them is how many agents each serves at the owner's conversation lengths. One 4-bit copy across both 3090s has an 850k
pool, holds 8 conversations of 128k at 35 tok/s each, and does 55 solved tasks an hour at 40k against IQ3_S's 16. Two
settings made that possible and neither is a default: one draft token instead of three (three cost a four-token
verify per seat per step, and the copy fell past 8 agents), and a Mamba checkpoint every five blocks instead of
every block (dense checkpoints fill the pool with saved states, and at 6 agents of 128k a returning conversation
was read again from the start, about 100 seconds during which every agent stalled).

**Big has no routine job.** Flash-Next IQ3_S on the hand split does the most work per token (41 solved per million
against the 27B's 34), decodes as fast as the 27B for one request and reads a 175k prompt four times faster, and it
holds its answers at 191k where the 27B slips. But it serves one request at a time, so it does a third of main's
work per hour, and the owner's local model is a tight implementation agent working from Claude's specs, where the
27B's coding is enough. Big stays a setup to switch to for one long read: a whole codebase or a long log in one
prompt. Q4 is not worth its speed: it ties IQ3_S on the hard set, decodes at 60% of its speed and still reads the
drive, and a coding expert order adds about 2%, unproven. The third card adds nothing to IQ3_S.

**The swarm is the 4-bit A3B, and its setting matters more than its model.** With all 100 scouts at once every model
spent its time reading conversations again: the pool turned over faster than a scout's turn came round. With as
many at once as the pool holds, the same areas take 28-30 minutes instead of 124 and find more (39-43 of 60 against
34). At that setting the 8-bit A3B does the same work in a third of the room, the 9B finds two thirds as many with
twice the noise, the 4B less, and the 27B, which reads first and reports at the end, barely reports without thinking
and takes five hours with it. Thinking, rolling context and smaller areas each cost more than they found. A second
independent pass finds about 4 more, and the spots both passes flag are the ones most likely real.

The 5060 Ti's 4B can swarm beside a busy main without slowing it (main 291-294 tok/s at 8 agents of 128k against 280
alone), at the 4B's quality. _Pending: what the 27B's check of the swarm's reports keeps._

## Decision

| Setup | Model and engine | Cards | Per agent | Load | Quality | Switch |
|---|---|---|---|---|---|---|
| **main** | Qwen3.8-27B, production's 4-bit build; vLLM (HyperQwen), tensor parallel 2, MTP with 1 draft token, `--prefix-cache-retention-interval 4080` (w4-27b-tp2m1r4080) | 3090 + 3090 Ti | 128k context (262k at most) | 8 agents at 128k (280 tok/s, 35 each), 16 at 40k (452); 81-90 tok/s alone | 30/42 held-out, 13/24 hard; with retries 12 of 14 tasks | 1-2 min |
| **swarm** | Qwen3.6-35B-A3B, 4-bit AWQ; vLLM, tensor parallel 2; no thinking, whole context (a3b-awq) | 3090 + 3090 Ti | 64k a scout | 100 areas of about 440 lines, as many scouts at once as 85% of the pool holds at 40k each (35) | 39-43 of 60 planted bugs, about 50 other reports, in 28-30 minutes | 3.5 min |
| **big** | Qwen3.8-Flash-Next IQ3_S; Strata, split by hand at layer 27, trimmed stages (fn-iq3s-2s) | 3090 + 3090 Ti | 262k | one request at a time, 118-121 tok/s; 175k read in 46 s | 32/42 held-out, 44/72 hard; most work per token | 1.5 min |

The 5060 Ti stays outside all three, free for small jobs beside main: the 4B on it swarms beside a busy main at no
cost to main (a wide, noisy first pass, about 23 found with 175-213 other reports in 65-71 minutes). The
scheduler should batch work by setup, run swarm jobs at the pool's fit rather than all at once, and send a long
read to big only when one prompt holds more than main reads in reasonable time.

## Lessons

- **Check a test's spread before reading it.** Two of this bench's tests ranked nothing on their first run: the
  long-context lookups (everyone 100%) and the coding grade on the box (everyone 0%, a missing account). Neither
  looked wrong from inside one arm's results.
- **Strata's container takes settings from environment variables only.** Arguments after the image name are
  dropped (`--parallel 2` was a silent no-op), and its setup starts whichever `strata-*.json` it finds in
  `/opt/strata`: an image committed from a running container carried one, and the next start asked "Which one?"
  with no terminal to answer. Commit only with those files removed; pass a config through `CONFIG`.
- **Strata serves one request at a time,** and its parallel mode costs expert cache. Many agents on Flash-Next queue.
- **One default configuration is not a verdict on a card.** The first pass called the 5060 Ti useless after one
  Strata run that gave it a third of the layers. The owner asked why the coldest experts could not sit on it the way
  they sit in RAM and on disk; Strata already does exactly that (helper expert caches, its `docs/SECOND_GPU.md`), and
  the same audit found a load test that stopped at 6 agents (a test limit reported as a server limit), tensor
  parallel never compared with pipeline parallel over the x4 link, and the 5060 Ti idle in the swarm. Those arms
  were added, and three of the four changed an answer: the 5060 Ti as a helper doubled Q4's decode; past 6 agents
  the 4-bit 27B fell, and one draft token instead of three kept it scaling to 490 tok/s at 16; pipeline parallel
  read cold prompts faster but decoded slower. (The 5060 Ti's own swarm is under Swarm.) Before writing "X adds
  nothing" or "Y tops out at N", read the engine's docs for the other placements and modes and test them.
- **A setup tool's defaults are not the box's limits.** Q4 read about 95 GB from the drive per answer, and the first
  reading blamed the 32 GB of RAM. The engine's tier log showed Strata's setup had given its RAM copy 8 GiB of the
  27.5 GiB free, by a rule written for a desktop that also runs a browser. Pinned to what the engine's guard allows
  (21 GiB), Q4's real answers decoded at 53 tok/s against 29; with 131k of context instead of 262k, at 62. Read what
  the engine allocated per tier before blaming the hardware.
- **Confirm each variant from the running engine, not from the config written.** The first helper run put the
  layers on the 3090 and the 5060 Ti and the experts on the 3090 Ti, the reverse of the plan: the committed Strata
  image carried `GPUS=0,1,2` from the container it came from, and that won over the config. A first check read the
  image's own stale log and looked right. Later, Strata's start-up rewrote `--prefill 2048` to `auto` (an upgrade path
  for old configs), so one Q4 arm ran as a repeat of another; the engine's command line showed it, the config did
  not. Read the log section for this start, the engine's command line and environment, and each card's memory.
- **Check that the server saw what the client did.** The load test marked Strata's requests cut, and its numbers
  looked like a slow engine; the engine log showed every request running to its end. A client-side flag is not
  evidence of a server-side effect.
- **Check that a load test reached the load it names.** The swarm sweeps said 16k, 32k and 64k, but every agent
  started fresh and none got past about 11k inside the window, so the three caps measured the same short
  conversations. The test's own token estimate also ran about 15% under the server's count, unnoticed until a 64k
  cap met a server that serves 64k and every long request failed. Read the server's numbers (its token count, its KV
  use) beside the test's own.
- **Test an agent loop against a model that asks for many things at once.** The scout's stub asked for one read a
  turn, and the larger models ask for five to ten. Three faults hid behind that: the context cap, checked between
  turns, was overshot by a whole batch of reads; rolling context dropped all but the last two reads of a batch
  before the model saw them, and counted them as read; and the A3B and the 27B, which read their whole area before
  reporting, hit the cap with nothing reported (the 27B's 32 scouts read 100% of their areas and made 5 reports). A
  stub that reads in batches found the second in one run.
- **A report near the answer is not the answer.** "Within 3 lines of a planted bug" credited the noisy 4B with 3
  finds that described something else. Have a judge compare the report with the planted change.
- **Reports off the answer key are not all false.** A hand check of the A3B's 28 reports away from every planted
  bug found 9 real bugs already in the owner's code; scoring them as false would have understated it by a third.
- **The engine's KV-use gauge hides the cache.** vLLM counts only the blocks running requests hold; a finished turn's
  cached blocks count as free until evicted. The 4-bit A3B's 100-at-once run showed 19% use with 78 requests waiting
  "for capacity". Read `num_requests_waiting_by_reason` and the prefix-cache hit counters beside it.
- **More agents than the pool holds is not a slower run; it is a different one.** Past the pool, each miss reads a
  whole conversation again, the reading evicts the next scout's conversation before its turn comes back, and the
  run becomes almost all prefill (7% of prompt tokens from the cache, 113 minutes). Capped at the pool's fit, the
  same work took a quarter of the time. Size concurrency from the server's own pool, not from a round number.
- **A grid that changes two things at once cannot say which mattered.** The grid's 100 scouts against 32 changed
  both each scout's area and how many ran at once; the 100 areas at the pool's fit separated them.
- **Explain a slower run from what it spent, not from the likely story.** Rolling context was first put down to
  re-reading; the engine's counters showed the cache share was low either way and the rolling scouts ran twice the
  turns, since they never met the cap that ends a whole-context scout. Count the turns and tokens before naming a cause.
- **A model that reads first and reports last needs its turn limit checked.** The 27B without thinking read every
  line of its areas and reported nothing in 84 of 100 scouts, its 40 turns spent before its report came. A test
  that ends agents by turns measures that habit as much as the model.

## Files

- `server/bench/bench-arms.sh`: start, stop and time every arm.
- `server/bench/bench-phase.sh`, `chain-rest.sh`, `chain-next.sh`, `chain-next4.sh` to `chain-next8.sh`,
  `chain-next10.sh` to `chain-next16.sh`, `pick-arm.py`, `skip-tp2b.sh`: the unattended phases and their order (each
  chain waits for the one before it, or takes over from it at a named run; chain-next6 refuses to start without a
  pid, after an empty one skipped its wait and ran it beside chain-next4's swarm). `bench-phase.sh`'s `pool_fit` sizes
  a swarm's scouts at once from the engine's pool.
- `server/bench/expert-profile.py`: learns a Strata expert order from a prompt set (the coding order, IQ3_S with a
  coding order).
- `server/bench/bench-watchdog`, `bench-watchdog.service`: phone pings while the bench runs unattended. It pings
  after 45 minutes with no new result and stops the arm at 90, so the chain moves on (the slowest healthy stretch
  is one coding answer on a Strata Q4 arm, 10-16 minutes). It also pings when nothing is left running and when disk
  space runs low. Disabled once the bench is done.
- `server/bench/bench-load.py`: speed and agents at once.
- `server/bench/longctx.py`: long-context lookups and the whole-prompt questions (`build-hard`).
- `server/bench/lcb-quick.py`: the coding runner and grader.
- `server/bench/scout.py`, `plant.py`: the scout swarm and the planted-bug target.
- `server/bench/hw-window.py`: power and heat per arm.
- `server/bench/hf-capped.py`: rate-capped model downloads (two uncapped downloads took the house offline).
- The held-out coding set, the long-context question sets and the planted-bug list stay private.
