# Experiments

Every change to the harness or the serving setup that could be measured was measured. This page gives the question,
the result and the decision for each experiment. Long write-ups have their own pages, linked below.

| Experiment | Date | Decision |
|---|---|---|
| [Hand-over notes in the task file](#hand-over-notes-in-the-task-file) | 2026-09-28 | Adopted: notes live in each task file |
| [Pi vs Oh My Pi](#pi-vs-oh-my-pi) | 2026-09-28 | Stay on Pi |
| [Two agents on one GPU](#two-agents-on-one-gpu) | 2026-09-28 | One agent per GPU |
| [Thinking effort: xhigh vs medium](#thinking-effort-xhigh-vs-medium) | 2026-09-30 to 10-01 | Stay on xhigh |
| [A mixture-of-experts model on the RTX 5060 Ti](#a-mixture-of-experts-model-on-the-rtx-5060-ti) | 2026-10-02 | Not a general coding agent; the card runs the dense 27B |
| [Model choice: 27B vs Flash-Next vs Swift](#model-choice) | 2026-10-05 | Keep the 27B; end capped thinking with a wrap-up sentence |
| [Thinking cap: 8k, 16k, 32k](#thinking-cap) | 2026-10-06 | 16k |
| [Type-1 log triage model](type1.md) | 2026-09-29 on | A small fine-tuned model leads; paused |

## Hand-over notes in the task file

**Question.** Should a session's hand-over notes move from one shared `PROGRESS.md` into a `## Hand-over` section of
the task it worked on? Two agents on two branches would conflict on every merge of a shared file, but the change only
helps if the agent actually follows it (it rewrote `PROGRESS.md` in 94% of sessions out of habit).

**How.** Task 025 of hollowdeep was replayed from the commit right after the agent split it, on a copy of the
project, twice per design. A separate test with no model simulated two agents merging each design.

| Run | Notes in | Sessions to finish 025 | Hours | Context at first code edit | Edit failures |
|---|---|---|---|---|---|
| A | PROGRESS.md | 10 | 1.06 | 64k | 13% |
| A2 | PROGRESS.md | 11 | 1.16 | 59k | 0% |
| B | task file | 10 | 1.17 | 68k | 14% |
| B2 | task file | 8 | 1.00 | 68k | 6% |

**Result.** The notes landed in the task file in 21 of 21 sessions; the driver's safety net was needed once.
Performance was the same within the run-to-run noise. Task-file notes merge cleanly for two agents. Adopted.

## Pi vs Oh My Pi

Oh My Pi (a Pi fork with hash-anchored edits and language-server tools) ran the same replay with the lab's
extensions. It finished the task but about 70% slower: a bigger built-in prompt (~9k vs ~3k tokens), more reading
before acting, more thinking (360k vs ~210k tokens), and 4 of 12 sessions cut off by the context limit instead of 1.
Its edits failed no less often (11%). One run, but far outside the noise. Stay on Pi.

## Two agents on one GPU

Two agents ran the same replay at the same time on the RTX 3090 Ti. Together they did ~1.06x the work of one: each
took about twice as long. Speculative decoding already uses the card's spare capacity, and the context memory (200k
tokens) is too small for two agents at production limits (243 preemptions in two hours). An earlier quick probe that
suggested 2.5x used short, non-thinking requests and was not representative. **One agent per GPU.**

This test also found a bug that would have broken team mode: the signal that ends a session was shared between loops,
so when one agent finished, both sessions stopped. Fixed before team mode was built.

## Thinking effort: xhigh vs medium

The model can be told how hard to think. A first test (one task, three runs per arm) was graded by a checker that
scored a contradiction in the spec rather than the code, so it decided nothing. The second test measured the parts of
the real job separately, 54 runs in all: reading specs with planted flaws, correctness on hidden checks, code quality
(blind judge, mutation score) and a full multi-session project.

- `medium` used about half the tokens and 55-60% of the time: roughly 1.8x the throughput.
- Hidden checks: no difference in correctness.
- A blind judge preferred `xhigh`'s code in 18 of 21 pairs (better tests and robustness; `medium`'s was simpler).
- `xhigh` noticed slightly more planted flaws (19/22 vs 17/22). At either effort, what turned a noticed flaw into the
  author's intended behaviour was asking.

Effort stays at `xhigh`. Full method and results: [effort-ab.md](effort-ab.md).

## A mixture-of-experts model on the RTX 5060 Ti

Qwen3.6-35B-A3B (3B active parameters) runs three times faster than the dense 27B on the 16 GB card, so it was tested
with the same probes. Its code worked on the hidden checks, but the blind judge preferred the 27B's in 21 of 23 pairs,
it noticed 6 of 16 planted flaws (27B: 13), and it rewrote the human's acceptance criteria 32 times. The card runs the
dense 27B instead. Details: [effort-ab.md](effort-ab.md#model-arm-qwen36-35b-a3b-mixture-of-experts-on-the-rtx-5060-ti-2026-10-0102).

## Model choice

The production 27B against Qwen3.8-Flash-Next (a 125B mixture-of-experts model on the Strata offload engine) and
Swift 1.5 (a 27B fine-tune that claims to think less), on a private set of 14 coding tasks the models cannot have seen.
The test found that production ended capped thinking badly: a bare end tag, after which the model kept reasoning in
its answer until it ran out of tokens. Ending it with a short wrap-up sentence instead took the 27B from 4 to 10 of
14, ahead of Flash-Next (7) and Swift (8). The 27B stays, and both servers now end capped thinking with that
sentence. Full write-up: [model-choice.md](model-choice.md).

## Thinking cap

Same 14 tasks, production settings, only the per-response thinking cap changed:

| Cap | Passed | Output tokens |
|---|---|---|
| 8k | 6 of 14 | 144k |
| 16k | 10-11 of 14 (two runs) | 231-237k |
| 32k | 10 of 14 | 437k |

On the hard tasks the model thinks until whatever cap it is given. Cutting it at 8k cost correctness; going past 16k
bought nothing. The cap is 16k. Details: [model-choice.md](model-choice.md).
