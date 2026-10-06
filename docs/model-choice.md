# Model choice, October 2026: Qwen3.8-27B vs Qwen3.8-Flash-Next vs Swift 1.5

Status (2026-10-05): tests finished; decided: keep the 27B. Deployed: the wrap-up ending on both servers and a 32k thinking cap (below). In short: on a fair footing
the production 27B is at least as good as both alternatives, and the biggest finding is not about the model at all.
Production vLLM ends a capped thinking block with a bare `</think>`, after which the 27B keeps reasoning in its
answer until it runs out of tokens. Ending it with a short wrap-up sentence instead took the same model from 4 to 10
of 14 held-out tasks.

## The question

Three options for the RTX 3090 Ti's coding agent:

1. **Keep** Qwen3.8-27B W4A16 (AutoRound) on vLLM HyperQwen with MTP, as in production.
2. **Switch to Qwen3.8-Flash-Next**, a 125B mixture-of-experts model with 6B active parameters, at IQ2_XS (about 2
   bits per weight) on [Strata](https://github.com/Niko1221/Strata). Strata is an expert-offload engine: all
   experts live in system RAM, the most used ones are cached in VRAM, and the CPU computes the rest.
3. **Switch to Swift 1.5** ([ukisai/Swift-1.5-Qwen3.8-27b](https://huggingface.co/ukisai/Swift-1.5-Qwen3.8-27b)), a
   fine-tune of the 27B advertised to think about 58% less and to score higher on LiveCodeBench.

Work per hour counts as much as correctness. In the human's words, 30% better results at half the speed would be
a real tradeoff, not an automatic win.

## Method

- **Speed:** `server/bench/engine-bench.py` on the 3090 Ti, thinking `xhigh`, temperature 1.0.
- **Capability, main test: a private held-out set.** Public benchmarks may be in a model's training data, or a
  model may be tuned for them, so the main test is 14 tasks written for this comparison and never published: 8
  practical application tasks (file and config formats, a rate limiter, evaluation, simulation and build-ordering
  tasks) and 6 algorithmic ones. All are stdin/stdout with 2 examples in the prompt and 11 hidden tests each (5
  small, 4 medium, 2 large). Every reference solution was checked against an independent brute-force solution on
  300 random inputs per task before any model saw the set. One attempt per task and model, with production request
  settings: `xhigh`, temperature 1.0, top_p 0.95, top_k 20, 16k thinking cap, 32k tokens per answer. The grade runs
  the answer's last code block against all tests (runner: `server/bench/lcb-quick.py`).
- **Secondary:** LiveCodeBench v6 (released January to April 2025, so possibly seen in training), 20 AtCoder
  problems (12 hard, 8 medium, fixed seed); and 6 runs of the [effort A/B](effort-ab.md)'s spec-reading probes
  through the real agent loop, for Flash-Next.
- No LLM judge: too expensive for a quick test.

The 27B arms ran on the production vLLM next to a working agent. That changes their wall time, not their answers,
so their speed is estimated as tokens divided by the measured solo rate.

## Results

### Speed

| 3090 Ti | Decode, short prompt | Decode, 52k-token prompt | Cold prefill, 52k | Cached time to first token |
|---|---|---|---|---|
| Qwen3.8-27B, vLLM + MTP | 106 tok/s | 99 tok/s | ~1,110 tok/s | ~1.5 s |
| Flash-Next IQ2_XS, Strata | 104 tok/s | 96 tok/s | 2,670 tok/s | 0.14 s |

Strata kept 97% of expert lookups in VRAM. Its low-RAM resident mode holds about 21 GB of experts in locked system
RAM, a lot on a 32 GB box. Swift ran at about 104 tok/s on the same vLLM. No HyperQwen "fast" variant exists for it
(a draft vocabulary counted over the base model's own outputs), so its MTP drafts are accepted somewhat less often.

### Held-out set (14 tasks)

| Arm | Pass | Algorithmic | Practical | Output tokens | Answers that hit 32k | Time, solo |
|---|---|---|---|---|---|---|
| Flash-Next, Strata | 7 | 4/6 | 3/8 | 233k | 0 | 40 min |
| Swift 1.5, vLLM, bare `</think>` | 8 | 5/6 | 3/8 | 315k | 5 | 50 min |
| 27B, vLLM, bare `</think>` (production today) | 4 | 1/6 | 3/8 | 333k | 7 | ~56 min (est.) |
| **27B, vLLM, wrap-up ending** | **10** | **4/6** | **6/8** | **237k** | **0** | **~40 min (est.)** |
| 27B, vLLM, wrap-up, 32k thinking cap (production since 2026-10-05) | 10 | 5/6 | 5/8 | 437k | 0 | 102 min, shared with an agent (not solo) |

Per task (A = algorithmic, P = practical; task names withheld with the set):

| | A1 | A2 | A3 | A4 | A5 | A6 | P1 | P2 | P3 | P4 | P5 | P6 | P7 | P8 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Flash-Next | . | P | P | P | . | P | . | . | P | . | P | . | P | . |
| Swift, bare | P | P | P | . | P | P | . | . | . | P | P | . | . | P |
| 27B, bare | P | . | . | . | . | . | . | . | P | P | P | . | . | . |
| 27B, wrap-up | P | . | P | P | . | P | . | P | P | P | . | P | P | P |
| 27B, wrap-up, 32k | P | P | P | P | . | P | . | . | P | P | P | . | P | P |

### LiveCodeBench v6 and probes

- LiveCodeBench, 20 AtCoder problems: Flash-Next 14/20 (hard 6/12, medium 8/8, 289k tokens); 27B with the bare
  ending 12/20 (hard 4/12, 401k tokens; 8 answers hit 32k, 6 of them with no code).
- Probes, 6 runs through the agent loop: Flash-Next passed every hidden check, handled 3 of 4 planted spec flaws
  the author's way (27B: 2 of 4), made no rejected done claims; median 3.35 min per run (27B 3.15).

Both 27B numbers here were measured with the bare ending, so they understate it, the same way as the held-out row.

## The thinking-cap ending

Nearly every held-out task used the full 16k thinking budget, on every model. What happens at the cap differs:

- **Strata** appends `I have thought about this long enough; time to give my answer.` and then `</think>`.
- **vLLM** (`thinking_token_budget`) forces only `</think>`: production sets no `--reasoning-config`, so the end
  string defaults to the reasoning parser's end tag.

After the bare tag, the 27B and Swift treated the cut as a pause and went on reasoning in the answer. The tail of
such an answer is reasoning text with code fragments in it. Because the grade runs the last code block, a fragment
of an example input or a half-finished line (`... ? Usually final newline`) became "the program". Flash-Next never
ran out of tokens, so part of its lead was the engine, not the model.

To compare on equal terms without touching production, `lcb-quick.py --wrap-up` gives a vLLM model Strata's ending
from the client. It streams the answer with no server cap. When thinking reaches 16k tokens with no answer started,
it closes the stream (vLLM aborts the request) and continues on `/v1/completions` from the prompt (from
`/v1/chat/completions/render`), the thinking and the wrap-up sentence, mostly from the prefix cache. With that
ending the 27B passed 10 of 14 instead of 4, with 30% fewer tokens and no answer at the 32k limit.

**Production fix (deployed 2026-10-05):** vLLM's `--reasoning-config` takes a `reasoning_end_str`, the string
forced at the budget, which may include a transition phrase before the end tag; it is now the wrap-up sentence plus
`</think>` (`server/vllm-hyperqwen/env.example`). llama.cpp on the 5060 Ti gets the same sentence from
`--reasoning-budget-message`. Both change only answers that hit the cap; a model that ends its own thinking is
unaffected. Tested on both servers with a 200-token budget: the thinking ends with the sentence, the answer is a
clean code block. With the clean ending in place, the cap went from 16k to 32k thinking tokens (the human: ending
the thinking that early cripples the model), and Pi's per-response `maxTokens` from 32k to 49k so the answer keeps
room after a full budget. In the agent loop the 16k cap fired in about 9% of sessions.

**32k cap, measured (2026-10-06):** the same held-out set with the production settings (32k thinking cap, wrap-up
ending, 49k per answer) passed 10 of 14, the same total as with the 16k cap (5/6 algorithmic instead of 4/6, 5/8
practical instead of 6/8: different tasks flipped both ways, within the noise of one attempt each), for 437k output
tokens instead of 237k. 12 of the 14 answers thought until the 32k cap, and the wrap-up ending closed every one
cleanly (no answer at the length limit). On this set the larger cap bought no extra passes for 1.8x the tokens; the
human's reason for it (an early end cripples the model on long agent sessions) is not what this set measures.

## Reading

- **Sample size.** 14 tasks, one attempt each: one task is 7 points, and P5, passed by every other arm, failed
  for the 27B with the wrap-up after it had passed with the bare ending. Fair 27B vs Flash-Next: 5 tasks only the 27B passed, 2 only Flash-Next
  (a sign test gives p about 0.45). That is no evidence Flash-Next is better, not proof that the 27B is.
- **Throughput.** Fair 27B and Flash-Next used the same tokens (237k vs 233k) at the same decode speed, so the
  same time per task. Flash-Next's real speed advantage is prefill: 2.4x faster cold, and 0.14 s against 1.5 s with
  a cached prompt. That matters for long agent prompts, but it comes with 21 GB of locked RAM, a 2-bit quant and a
  young engine.
- **Swift** beat the base model under the same bare ending (8 vs 4) but was never rerun with the wrap-up, which
  needs the 3090 Ti and a production stop. The advertised shorter thinking did not show here: Swift hit the 16k
  cap on 12 of 14 tasks, like the base model, and used about as many tokens. Running it also needs a hand-repacked
  checkpoint (below) and gives up the fast variant.

## Decision

Keep the 27B, with the wrap-up ending and a 32k thinking cap. Flash-Next and Swift are set aside, and their files
deleted, until the engine matures (Strata squeezing more out of the model) or the next Qwen generation; Flash-Next
would be worth another look if prefill latency becomes the bottleneck or the box gets more RAM.

## Swift on HyperQwen: the checkpoint repack

The official 4-bit Swift build (`ukisai/Swift-1.5-Qwen3.8-27b-W4A16-AutoRound`) uses the same method as production
(AutoRound, symmetric int4, group size 128) but stores it in auto_gptq layout (`qweight`/`qzeros`/`scales`).
HyperQwen's preparation scripts and kernels expect compressed-tensors (`weight_packed`/`weight_scale`/
`weight_shape`). `server/vllm-hyperqwen/autoround-gptq-to-ct.py` repacks one layout into the other without loss:
the same int4 values and the same fp16 scales, checked by a round trip on every layer. Then HyperQwen's
`quant_heads_stream.py` and `build_draft_vocab.py` ran as for any other checkpoint.

Check after the repack: Swift's dequantized weights against the base 27B's, on six layers from the first to the
last. Correlation 0.995, the same magnitude, and a row-mean offset of 0.003 quantization steps. A mistake in the
zero point would show as an offset of about 1.

## Files

- `server/bench/lcb-quick.py`: the runner (LiveCodeBench or a held-out file via `LCB_DATA`; `--wrap-up`).
- `server/vllm-hyperqwen/autoround-gptq-to-ct.py`: the Swift checkpoint repack.
- `analysis/ab-effort2/run.py`: the probes' Strata arm (`plan-strata`, `ab-launch-strata`).
- The held-out set and its generator stay private.
