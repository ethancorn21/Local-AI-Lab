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
| [Baseline: where a card's day goes](#baseline-where-a-cards-day-goes) | 2026-10-08 | The baseline to beat: 42% of the day generating, 31.5% on tests |
| [Test quality: what the tests catch](#test-quality-what-the-tests-catch) | 2026-10-08 | One test run per task instead of three; timing tests out of the per-task run; no direct-call tests |
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

## Baseline: where a card's day goes

**Question.** With the team rules in place (cycles, takeover, carving: deployed 2026-10-07 21:43), what share of each
GPU's day is spent generating, and what is the rest spent on? The lockup before those rules is left out.

**How.** frontpage, three agents, 2026-10-07 21:43 to 10-08 19:44 (22 h). Model time from each server's own counters:
tokens generated and prompt tokens per minute (lab archive) times seconds per token from the servers' lifetime timing
totals. Session, waiting and driver time from the ledgers, the team event log and the loop logs. Time inside a session
when the card was idle is split by the tool each turn was waiting on. Scripts: [analysis/baseline/](../analysis/baseline/).

| Share of the day | a, RTX 3090 Ti | c, RTX 3090 | b, RTX 5060 Ti | Average |
|---|---|---|---|---|
| Generating | 28.6% | 41.3% | 56.3% | **42.1%** |
| Reading the prompt | 8.4% | 10.7% | 2.6% | 7.2% |
| Tests the agent runs in its session | 17.8% | 11.9% | 7.7% | 12.5% |
| Tests the driver runs (baseline, verify, after merge) | 22.9% | 22.2% | 11.9% | 19.0% |
| Other tools (shell, installs, file edits) | 10.5% | 7.9% | 4.9% | 7.8% |
| Other driver work | 6.6% | 2.3% | 3.8% | 4.2% |
| Waiting, nothing it may take | 5.3% | 3.5% | 12.7% | 7.2% |

| Baseline per day | |
|---|---|
| Acceptance boxes delivered (ticked, verified, in main) | ~187 |
| Verified tasks merged | 39 |
| Tokens generated | 6.34M (a 2.31M, c 3.03M, b 1.00M); 17.2M if every card generated all day |
| Prompt tokens read | 278M, 96% from the prefix cache |
| Output tokens per acceptance box | ~34k |

**Result.** 58% of each card's day is not generating, and 31.5% is test runs. The driver alone runs the full suite
about three times per task, ~18 minutes: before the task's first session (a baseline, median 6 min), to verify the
done claim (6.5 min) and again after merging main (5.5 min). The harness VM's CPUs were 85% idle that day (peak load
2.3 on 6 cores), so the suite is slow because it runs serially, and any run that includes the timing tests holds the
team-wide test lock alone. The 5060 Ti generates the largest share only because it is slow; the same waits are a
smaller part of its day.

**Decision.** This is the baseline. Changes are judged by acceptance boxes per day; the generating share and tokens
per box explain why it moved. Tokens per day alone are not work: more thinking, polling turns or rejected claims all
raise it. Candidate changes, by time at stake: reuse the test result of a tree that was just tested instead of a new
baseline run (~7.5%), verify in the background while the agent starts its next task (~11.5%), a parallel suite with
the timing tests outside the shared run, targeted tests inside sessions, a streamed plan (~7% waiting), and a re-run
of the two-agents-per-card test now that the cards sit idle half the day.

## Test quality: what the tests catch

**Question.** The driver runs the full suite before a task (baseline), at the done claim (verify) and again after
merging main; agents run it during sessions too. Tests are 31.5% of each card's day. Which of these runs catch real
bugs, and how good is the suite itself?

**How.** Three sources. (1) Every driver run on frontpage since 2026-10-02 (loop logs, decision journals). (2) Every
failing test run inside a session since 10-06: was the failing test older than the session, and did the agent then
change app code or test code? (3) Mutation testing on a clone of main (04cf1bc) on a Mac: 48 single-point bugs planted
in the app (Python, page JavaScript, templates: flipped comparisons, deleted statements, changed constants, dropped
template values), the suite run on each with the timing tests left out, and each catch attributed to the kind of test
that failed (classified statically: browser, HTTP request to the app, direct call of app functions, golden file).
Scripts: [analysis/test-quality/](../analysis/test-quality/).

| Run | Count | Real bugs it caught |
|---|---|---|
| Baseline before a task's first session | 159 | 0 (6 found reds, all already known) |
| Verify at the done claim | 150 claims | 0 of 5 test-related rejections: 1 timing flake, 2 suite timeouts caused by the test lock, 2 tests out of step with an intended change |
| Re-run after merging main | 149 merges | 0 failures |
| Agents' own runs (since 10-06) | 687 | 21 failures of tests older than the session: 0 fixed by app code alone, 10 by editing the test, 6 passed on a re-run |

| Mutation result (48 planted bugs) | |
|---|---|
| No user-visible effect (chunk size, an unused embedder, a boundary never hit) | 6 |
| Caught by the suite | 37 of 42 (88%), one of them as a hang |
| Missed | 5: four in the page JavaScript (infinite scroll dropping the filter or the sort; the seen timer), one config check |
| Caught by browser tests | 17; 7 only by them (all JavaScript) |
| Caught by HTTP tests, never by a browser test | 20 (sources and read pages, fetchers, migration, a missing commit) |
| Caught by direct-call tests | 8; 1 by nothing else |

| Suite time by kind (Mac, 629 tests, 185 s) | Tests | Share of time |
|---|---|---|
| Timing tests (test_perf.py, 12k-item corpus) | 6 | 75% |
| Browser | 16 | 19% |
| HTTP | 488 | 6% |
| Direct calls | 114 | 0.2% |

**Result.** The suite is good at catching bugs (88%), but in practice it almost never catches one: the agents rarely
break existing behaviour, and when an old test fails they edit the test about as often as anything else. Running it
three times per task buys nothing over running it once. Six timing tests take three quarters of its time, catch speed
regressions only, and hold the team-wide test lock alone, which caused waits and two false rejections. Direct-call
tests (a quarter of the test functions) added one catch of their own. Browser tests alone would have caught 17 of the
42 bugs; the HTTP tests cover pages and pipelines the browser tests never reach. Agents wrote about three lines of test
per line of app code in the 10-07/08 sprint. Side findings: the browser tests hard-code the Linux browser path (they
cannot run on the Mac), and the pinned feedparser 6.0.10 does not import on Python 3.13+.

**Decision.** Proposed, not yet decided: one full run per task (merge main first, verify once; baseline from the last
result for the same tree); timing tests out of the per-task run, on a schedule instead; no new direct-call tests.

