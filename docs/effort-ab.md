# Thinking effort A/B: does `xhigh` vs `medium` change how the lab works?

Status (2026-10-01): round 2 is finished and judged; results in "Round 2 results" below. In short: `medium`
used about half the tokens and 55-60% of the wall time for the same correctness and judged code quality; `xhigh` read
specs slightly more critically and wrote somewhat stronger tests on the project. The planted flaws and their answers
are in the appendix; the hidden files themselves (`analysis/ab-effort2/hidden/`: checks, seeds, judge packets and
verdicts) are not published yet.

## The question

The human's complaint is throughput, work done per hour, not reply latency. The model already writes at about 100
tokens/s, near what one RTX 3090 Ti can do for one agent, so the lever is **tokens per task**. At `xhigh`, about 71%
of what the model generates is hidden thinking. If `medium` does the same work with less thinking, projects finish
sooner. If it saves tokens but reads specs worse, writes worse code, or needs more sessions, it is not a saving.
The question is: *does effort meaningfully affect the way the human works with the agent?* That covers reading
a spec critically, code worth keeping, and carrying a project through many sessions of the loop, not only "can it
code". Qwen3.8 exposes three effort levels through the chat template: `low`, `medium`, `xhigh`.

## Round 1 (v1), 2026-09-29

`analysis/ab-effort/`, run by another Claude session. Six copies of the type1-triage project at the start of its
task 004, a streaming log windower that must produce exactly the windows of a reference function
(`canon.windows`) while events arrive up to 10 s late. Arms alternated `xhigh`/`medium` (X1 M1 X2 M2 X3 M3). A
hidden grader, never shown to the agent, fed 300 random shuffled streams and compared the output with the reference,
then checked the flush timer and the late-event drop.

As first reported: X1, M1, M2 passed everything. X2 (90 min) and X3 failed "equivalence" at 161/300 and the flush
check. M3 scored 279/300. The reading at the time was "xhigh failed twice, medium passed".

## Audit of round 1 (2026-09-30)

A second session checked the work. Findings, most important first:

1. **The grader marked the better answer as a failure.** Task 004's spec contradicts itself. Rule (c) says to flush
   a quiet stream 60 s after its newest event, and the spec also says the output must be exactly what the reference
   gives, while events may arrive up to 10 s late. Case: events at ts 0, 2, 5 are flushed at 65.5; then an event with
   ts 58 arrives at 66 (8 s late, allowed). The reference puts it in the same window as 0, 2, 5; a windower that
   already flushed starts a new window. Run on every arm:

   | Arm | Late-event case |
   |---|---|
   | X2, X3 (xhigh) | matches the reference: they delay the flush to `max(high-water, first + 10 s) + 60 s`, and X3's docstring explains why |
   | X1, M1, M2, M3, and the live type1-triage windower | splits the window, so it does not match the reference |

   The grader's final tick at +61 s came before X2/X3's delayed flush, so their last window never came out.
   Re-graded with the final tick far enough out for either reading:

   | Arm | Effort | Minutes | Output tokens | Windows equal to the reference (late tick) |
   |---|---|---|---|---|
   | X1 | xhigh | 8.1 | 47k | 300/300 |
   | M1 | medium | 8.2 | 44k | 300/300 |
   | X2 | xhigh | 90.1 (a hung test, see 2) | 83k | 300/300 |
   | M2 | medium | 7.0 | 38k | 300/300 |
   | X3 | xhigh | 14.7 | 85k | 300/300 |
   | M3 | medium | 4.9 | 27k | **279/300, a real windowing bug** |

2. **X2's 90 minutes were a harness gap, not effort.** Its own test looped forever, and Pi's bash tool had no default
   timeout, so three sessions sat idle until the 45-minute session limit. Since fixed in three layers: a default bash
   timeout (600 s) extension, a stall watchdog in the driver (a session whose output stops growing for 15 min is
   killed with all its children, and the next session is told what hung), and no flaky-test rerun when the driver's
   own test run timed out.
3. **Timing was clean.** vLLM's own logs show at most one running request during the whole A/B, so no other work
   competed for the GPU.
4. Smaller: the harder grader being written next (grade2) had the same end-of-stream tick in every category; its
   hang guard subclassed `Exception`, so graded code with `except Exception` would swallow it. The round-2
   type-1 relabelling step crashed on a numpy bool in `json.dumps` (fixed and rerun). In the type-1 results, the
   0.1% false-positive threshold picked on validation gave 1.06% on test (10x), which the write-up now says.

**Corrected round-1 reading:** on correct windows, 5 of 6 arms are equal and one medium arm has a real bug; on the
spec's hidden contradiction, 2 of 3 xhigh arms handled the case the spec's own correctness rule demands and no
medium arm did; medium used fewer tokens. Three runs per arm, one task, one grader: this decides nothing, but it
showed that "passes a hidden grader" is not the thing to measure.

**Lesson:** a hidden grader has to separate *how the spec was read* from *whether the code is correct*, and it has to
be tested against wrong-but-defensible implementations, not only against the one reference its author had in mind.

## Round 2 (v2): what the human asked for

The human's requirements (2026-09-30): add spec reading, code quality judged by Claude, and multi-step project
work inside the loop, because that is how the model is used, "so we need these numbers, not just 'they can code'".
Decisions: a fresh security tool as the project; GPU time "however long you need"; requests to the human answered
by an auto-responder; this session builds and runs it; judging accuracy over judging cost.

### What is measured

| Area | How |
|---|---|
| Spec reading | Six one-task **probes**: four specs with one planted flaw each (a contradiction, missing information, two valid readings, a wrong file/function name) and two **clean controls** with no flaw. Two more flaws are planted in the project's GOAL.md. A blind Claude judge reads what the agent wrote (requests, plans, decisions, hand-overs, commit messages, comments) and decides whether each flaw was noticed and how it was handled: asked the human, wrote down an assumption, proposed a change, or silent. The controls give the **false-alarm rate**, without which an agent that complains about everything would look like the best reader. |
| Correctness | Hidden checks per probe, and a hidden end-to-end suite for the project: 21 hand-written rule cases, 40 random scenarios compared with a reference implementation, input handling (gzip by content, several files merged by time, unreadable files, garbage and undecodable lines), output format and order, and a 500,000-line timing case. |
| Code quality | Blind Claude rubric (1-5 on correctness risk, readability, design, tests, robustness, simplicity; overall 1-10), rep-matched head-to-head comparisons of the two efforts (probes and project, each pair judged in both orders), the **mutation score** of the agent's own tests, and ruff issues per 100 lines. |
| Project work | `authwatch`, a command-line detector for SSH brute force, password spraying, a login right after failures, and firewall port scans (JSON-lines output for a SIEM), built from a GOAL.md through the whole loop: planning task, the agent's own task queue, goal checks. Wall time, sessions, output tokens and thinking share, rejected done claims, stalls, tasks the agent added, requests to the human. |

Schedule: 54 runs. 48 probe runs (6 probes x 4 repetitions x 2 efforts) and 6 project runs (3 repetitions x 2
efforts), efforts alternated in ABBA order so any drift over the day hits both equally. Production settings
everywhere except `THINKING`; the driver and extension hashes are recorded per run.

### Keeping it fair

- **One agent on the GPU.** Each run starts only after the model server has had no running or waiting request for
  60 s; vLLM load is sampled every 20 s during the run, so any overlap shows up in the data and that run can be redone.
- **Hidden means hidden.** Answer keys, checks and the reference implementation live in a kit directory in the admin
  account's home (mode 700); the agent account cannot read it. Checks are copied into a temporary directory only
  after a run has ended. Run directories have neutral names (a probe is `r017/fwtop`, not `r017/p_contra`). Nothing
  hidden was published while the runs were going (the agent has web search).
- **Flaw handling never changes the core score.** Each planted flaw has its own category in the hidden checks. For
  the project's rule-window flaw, random-scenario detections whose verdict depends on the window (anywhere from 60 s
  to 180 s) are removed from both sides before comparing, so an arm that followed the worked example instead of the
  rule is scored on that only in the flaw category. Verified with deliberately wrong variants: 75 s and 120 s
  windows keep core 1.0 and fail only the flaw; an unreasonable 200 s window loses core points.
- **Requests to the human.** An auto-responder answers `ask_human` requests within ~20 s from the run's answer key:
  the local model (thinking off, temperature 0) classifies which planted flaw a request is about, and the reply is
  that flaw's resolution; anything else gets "choose what fits, write the assumption down". A classifier error leaves
  the request open for the next poll rather than sending a wrong answer. A self-test routes 11 known requests before
  any run. The doorbell is replaced by a stub for the test, so nothing reaches Telegram.
- **Blind judge.** Headless `claude -p` on the subscription, model `claude-opus-5-5[1m]` (1M-token context, so no
  packet is ever trimmed), `--effort high`, its own system prompt, `--setting-sources project` from an empty
  directory and `--tools ""`, which keeps user settings, hooks, plugins, CLAUDE.md and memory out (checked: the judge
  reports none). Packets are anonymous; run ids, paths and effort words are scrubbed, and the pull step scans for
  leaks.
- **Judge accuracy, measured rather than assumed.**
  - *Calibration:* before any real judging, the judge grades synthetic packets with a known right answer (an agent
    that documented a flaw, one that stayed silent, one that asked, false alarms on a clean spec, a correct solution
    vs a buggy one with hollow tests, in both head-to-head orders). Judging refuses to start until it passes.
    Result: 9/9.
  - *Quote verification:* every "noticed" verdict must quote the agent's words exactly; the script checks the quote
    is really in the packet, and a verdict whose quote is not found counts as not noticed.
  - *Retest:* a seeded 20% of packets is judged twice to measure the judge's consistency with itself.
  - *Behaviour from the checks:* what the code actually does at each flaw comes from the hidden checks, not the judge.
  - *Uncertainty:* the report gives Wilson 95% intervals for rates and a sign test for head-to-head wins.

### Known limits (read the results with these in mind)

- Three project runs per effort is still small; the probes carry most of the statistical weight.
- The auto-responder answers in seconds; a real human answers in hours. Asking is cheaper in the test than in life.
- The judge is one model family. Calibration, blinding and quote checks limit, but do not remove, its taste.
- Sampling runs at temperature 1.0, so run-to-run variance is real; results are reported per run as well as per arm.
- The planted flaws are one author's choice of what a flawed spec looks like.

## Building round 2: problems caught before any GPU time was spent

Every check was tested against implementations with a known answer before use. That caught:

1. **The flaw leaked into the core score** (see "Keeping it fair"): found by grading wider-window variants of the
   reference; fixed by neutralizing window-dependent detections.
2. **A probe check could not tell two readings apart:** its tie data made "first seen" order identical to "descending"
   order. Fixed the input order.
3. **A probe check expected the wrong value** (an address masked to the wrong prefix). Caught because a known-correct
   implementation failed it.
4. **An unplanned second contradiction in a probe.** Its seed test asserted the parser's exact output, while the task
   asked to add a field *and* said the existing tests must keep passing. Caught when the mutation-score tool refused
   to run on a red baseline. Fixed the seed test to check fields individually.
5. **A probe check depended on where the agent put its code**; it now credits the behaviour wherever it lives and
   records the location separately.
6. **The plumbing test launched the real loop.** The runner wrote its launcher to whatever path the test override
   pointed at, overwriting the stub. The stray loop was waiting for the (stopped) model server and was killed before it
   ran a session. Fixed; the stub test then passed end to end (set up, launch, request filed, auto-answered, graded).
7. **A wrong answer on a hiccup:** the responder sent the generic reply when the classifier call failed. It now leaves
   the request open and retries.
8. **A leak into a public file:** the classifier self-test cases described the planted flaws in plain words and were
   committed to the public repo. Moved into `hidden/` and the commit amended before any push.
9. **Judge isolation:** `--bare` cannot use the subscription (it needs an API key); isolation is done with a custom
   system prompt, `--setting-sources project` and no tools instead.
10. **Calibration, first run: 3 misses of 8.** Two were the quote checker (the judge prefixed quotes with the file
    name; quote and location are now separate fields). One was a bad calibration case: its "silent" agent actually
    stated a year rule, and the judge rightly counted that as noticing the flaw. After the fixes: 9/9.

## GPU hand-over (2026-09-30 night)

The GPU was running type-1 training (round 2 of the log-triage model bake-off, see `type1.md`). The human ruled the
A/B the higher priority: the RTX 5060 Ti for the type-1 model and the SIEM are not set up yet, while effort decides
how every project runs. The session running type-1 work was closed and a new one put on hold.

- The type-1 queue was stopped *behind* its current job: the job that saves the chosen model's weights finishes, then
  the queue ends, the training script restarts vLLM, and the A/B's sequencer starts. It was stopped by emptying the
  queue file in place. The queue runner reads that file one line at a time from an open descriptor (its read offset
  sat exactly at the end of the second line), so it reaches end-of-file after the current job; nothing was killed and
  the job's thermal watchdog kept running. The remaining three type-1 jobs are saved in `queue5.full.tsv`; resume
  after the A/B, with vLLM stopped, with `./run_queue.sh queue5.full.tsv` (finished jobs are skipped).
- The auto-mode safety classifier first refused that change (and some AI box file deletions) because the request came
  from another Claude session, not the human. It ran once the human authorized it directly.
- All agent loops stay paused for the whole A/B, including type1-triage. The human pauses the local-only project's
  loop.

## Round 2 results (runs 2026-09-30, judged 2026-10-01)

All 54 runs finished (probes 04:46-08:39 UTC, project 08:39-16:13 UTC) with no GPU contention sample, no stall, no
timeout and no session error. The auto-responder routed all 9 requests to the human correctly. Numbers below are
xhigh vs medium.

**Short version:** medium did the same work for about half the tokens and a bit over half the wall time, and the
hidden checks found no correctness difference (the one real bug is in an xhigh run). The code is not equal, though:
shown the two efforts' probe solutions side by side, the blind judge preferred xhigh's in 18 of 21 decided pairs,
mostly for its tests and robustness, and preferred medium's for simplicity. Scored one at a time on a 1-10 scale, the
same code came out tied, so the difference is real but modest. On the project (3 pairs) there was no overall winner.
xhigh read specs slightly more critically: it noticed 19 of 22 planted flaws vs 17, raised about twice as many other
concerns, and was the only arm to write down its reading when a spec allowed two. Neither effort built the spec
author's intended answer more often, because that came from asking, and both asked about as often.

### Cost

| | Probes xhigh | Probes medium | Project xhigh | Project medium |
|---|---|---|---|---|
| Runs | 24 | 24 | 3 | 3 |
| Wall time, median (total) | 3.3 min (124.5) | 1.7 min (58.9) | 95.5 min (285) | 50.4 min (161) |
| Output tokens, median (total) | 19.9k (720k) | 8.3k (318k) | 518k (1.50M) | 246k (0.76M) |
| Thinking share of output, median | 70% | 49% | 68% | 55% |
| Sessions, total | 30 | 28 | 52 | 49 |
| Tasks the agent added | 1 | 0 | 33 | 30 |
| Python lines written, median (tests) | 157 (104) | 117 (77) | 2,710 (1,756) | 2,249 (1,478) |

Per project repetition, xhigh took 1.8x, 1.9x and 1.7x the wall time of the matching medium run, and 1.9x, 2.1x and
2.0x the tokens. Medium also writes less visible text and code, not only less thinking. It did not need more
sessions to finish the project (17 each, median), so the saving is real throughput: about 1.8 projects per GPU-hour
for every one at xhigh.

### Correctness (hidden checks)

- **Probes:** all 48 runs score 1.0 on the core checks.
- **Project:** 5 of 6 runs score 1.0. r053 (xhigh) scores 0.966 because of a real bug: rsyslog compresses repeats
  into one line ("message repeated 5 times: [ Failed password ... ]"), which GOAL.md says counts as N attempts at
  that line's time. r053 counts it as one, so a brute force logged that way is missed (1 of 21 fixed cases, 5 of 40
  random scenarios). The judge found the same bug independently.
- **Speed:** every project run handled the 500,000-line case in 5-10 s (limit 45 s).
- **Two grader fixes after the runs.** Both are ambiguities nobody planted, scored outside the core like the planted
  flaws, and the old grades were kept (`grade.v1.json`):
  1. GOAL.md sorts output by `first_seen`, printed to the whole second. When two detections share a second, two runs
     (r052 xhigh, r054 medium) ordered them by the exact sub-second time. The check now accepts either order and lists
     those cases (`tie_order_by_exact_time`).
  2. The probe run directory was named `failcount`, the same name as the package the task asks for, so two runs
     (r023 xhigh, r024 medium) put `count.py` at the project root. That is a harness naming accident, not the agent's
     error; the check now accepts it.

### Spec reading (blind judge, every "noticed" verdict backed by a quote found in the packet)

| Flaw | Type | Noticed: xhigh | medium | Built the author's answer: xhigh | medium |
|---|---|---|---|---|---|
| F1 (project) | contradiction | 3/3 | 3/3 | 2/3 | 2/3 |
| F2 (project) | missing information | 3/3 | 3/3 | 1/3 (rollover 2/3) | 1/3 (rollover 1/3) |
| P1 | contradiction | 4/4 | 4/4 | 4/4 | 4/4 |
| P2 | missing information | 3/4 | 3/4 + 1 partial | 1/4 | 0/4 |
| P3 | two valid readings | 2/4 | 0/4 | 0/4 | 0/4 |
| P4 | wrong file/function name | 4/4 | 4/4 | 4/4 | 4/4 |
| **All** | | **19/22** (95% CI 0.67-0.95) | **17/22** (0.57-0.90) | | |

How flaws were handled (judge): xhigh asked 4, documented an assumption 12, proposed a change 3, silent 3 (one of
them silently right); medium asked 4, documented 9, proposed 4, silent 5. Other concerns raised: xhigh 48, of which
27 legitimate (56%); medium 26, of which 11 legitimate (42%). On the two clean controls (nothing planted), xhigh raised
15 concerns in 8 runs (7 legitimate, 8 false alarms) and medium 8 (3 legitimate, 5 false alarms): xhigh comments more,
and more of it is useful, but it also produces more noise.

What the table shows:

- **Obvious flaws are caught at either effort.** A contradiction between a rule and its example (F1, P1) and a wrong
  file name (P4) were noticed in every run.
- **Two valid readings is where effort showed.** All 8 P3 runs used the line's local date (the author wanted UTC;
  both are defensible), but only 2 xhigh runs said so in writing. Silent choices are the dangerous kind, because the
  human never learns a choice was made.
- **Noticing is not enough; asking is what delivers the author's intent.** All 6 project runs noticed that BSD syslog
  lines have no year (F2). The four that wrote down "current year" as their assumption break on a December log read
  in January and on last year's file; only the two that asked (r053 xhigh, r054 medium) built the author's rule. The
  one P2 run that produced the author's IPv6 form (r014, xhigh) spent 38 minutes and 207k tokens on it and never
  wrote the gap down. Asking was not effort-dependent: 4 asked flaws in each arm, and in the project medium sent
  more requests (5 vs 2).

### Code quality

| | Probes xhigh | Probes medium | Project xhigh | Project medium |
|---|---|---|---|---|
| Judge overall, 1-10 (mean) | 7.21 | 7.21 | 6.33 (5, 7, 7) | 7.00 (7, 7, 7) |
| Judge correctness risk / tests, 1-5 | 3.83 / 3.83 | 3.92 / 3.62 | 3.00 / 3.67 | 3.67 / 3.67 |
| Judge design / simplicity, 1-5 | 3.96 / 4.08 | 4.25 / 4.25 | 3.67 / 3.00 | 4.00 / 3.67 |
| Mutation score (mean) | 0.91 | 0.91 | 0.88 | 0.78 |
| Ruff issues per 100 lines (median) | 0.14 | 0.18 | 0.67 | 0.84 |

Head-to-head: each xhigh run against the medium run of the same task and repetition (24 probe pairs, 3 project
pairs), each pair judged twice with the order swapped. A pair's winner is the effort that won more of its two
verdicts; a 1-1 split counts as a tie. p is a two-sided sign test over pairs (the two orders of one pair are not
independent, so the test does not count them separately).

| Dimension | Probes: xhigh wins | medium wins | tie/split | p | Project: xhigh | medium | tie/split |
|---|---|---|---|---|---|---|---|
| **Overall** ("which would you rather maintain and trust") | **18** | 3 | 3 | **0.001** | 0 | 1 | 2 |
| Tests | **20** | 2 | 2 | **<0.001** | 3 | 0 | 0 |
| Robustness | **15** | 3 | 6 | **0.008** | 2 | 0 | 1 |
| Design | 14 | 5 | 5 | 0.06 | 2 | 0 | 1 |
| Correctness risk | 12 | 4 | 8 | 0.08 | 1 | 1 | 1 |
| Readability | 10 | 10 | 4 | 1.0 | 0 | 2 | 1 |
| Simplicity | 5 | **16** | 3 | **0.03** | 0 | 3 | 0 |

Probe overall winners by task: xhigh 4 of 4 on `c_failcount`, `p_ambig` and `p_contra`, 3 of 4 on `c_sums` and
`p_wrongref`; on `p_missing` medium won 2 and 2 were ties.

Why the rubric and the head-to-head disagree, and whether the head-to-head can be trusted:

- **Absolute scores compress.** Asked for a 1-10 score one solution at a time, the judge gave most probe solutions a 7;
  small but consistent differences only show when two solutions are side by side. This is the usual reason
  experiments use pairwise judging.
- **Not a position effect:** across all 54 verdicts the first-shown solution won 28 times and the second 24.
- **Mostly not a length effect.** xhigh writes about a third more code and tests, and judges are known to favour
  longer answers (the prompt told it not to). When xhigh's solution was the larger one it won 30 of 36 overall
  verdicts; when it was the smaller or equal one it still won 8 of 10. Few cases, but the preference does not depend
  on size.
- **What the judge preferred, in its own reasons:** broader tests (more edge cases: unusual log lines, IPv6, bad
  input), and input handling that does not silently miscount. Example from a `p_ambig` pair (A was xhigh, B
  medium): "B's strict pattern [...] misses real sshd lines with empty or space-containing usernames [...] A's tests
  are broader and well labeled."
- **The other measures do not see it.** Every probe solution passes the hidden checks, and the probes' mutation
  scores are equal (0.91 mean, median 1.0 at both efforts). Both sit near the ceiling on small tasks, so they cannot
  separate good from slightly better; the judge's preference is about edge cases the checks did not test. So it is a
  preference for sturdier small-task code, not evidence that medium's code is wrong.

Other code-quality notes:

- On the project, xhigh's lower rubric mean is one run: r053, the one with the repeated-message bug and duplicated
  machinery (overall 5).
- The project's own tests were stronger at xhigh: mutation score 0.90, 0.93, 0.83 vs 0.83, 0.65, 0.88, and the judge
  preferred xhigh's tests in all 3 project pairs. Medium's project code was simpler (3 of 3). Three pairs: a lean, not
  a result.
- r040 (a probe) has no mutation score: its logic is one regular expression, and the mutation tool changes
  operators and constants only, so there was nothing to mutate. A limit of the method worth knowing: regex-heavy
  parsing code is invisible to it.
- **A confound in the project code rubric.** The code judge reads GOAL.md, which still contains the F1 contradiction,
  but not the author's answer to a request. So every project run lost points over rule 1 one way or the other: the two
  that followed the worked example (r049, r050) for departing from the rule, the four that asked and followed the
  author's ruling for "contradicting the spec's own worked example". It hits both efforts alike, so it adds noise
  rather than bias.

### Running the loop

| | xhigh | medium |
|---|---|---|
| Done claims rejected by the driver (probes + project) | 1 (0 + 1) | 5 (2 + 3) |
| Requests to the human (probes + project) | 3 (1 + 2) | 6 (1 + 5) |
| Goal-check rounds (project, total) | 4 | 3 |
| Stalls, timeouts, hard caps, session errors | 0 | 0 |

Medium claimed "done" too early more often (the driver's check of boxes and tests caught each one and reopened the
task; every run still finished). Five against one is a small count, but it is the one loop-level signal that leans
toward xhigh.

### Judge reliability

- Calibration before judging: 9/9 known-answer cases right.
- Evidence quotes: 37 of 37 "noticed" verdicts quote text that is really in the packet; no invented evidence.
- Consistency (a seeded 20% of packets, 11 runs, judged a second time): the spec "noticed" verdict was identical for
  9 of 9 planted flaws (7 runs; the other 4 were clean controls); the code overall score moved by 0.45 points on
  average (1-10 scale).
- Head-to-head order: the two orders of a probe pair agreed on the overall winner in 19 of 24 pairs.
- The first judging pass stopped on the subscription's session limit (HTTP 429) after the spec and code judgments;
  the failed calls were not saved, and a rerun after the limit reset finished the rest. `judge.py` now stops on the
  first 429 instead of failing through the remaining calls.
- Judge tokens: about 1.98M input and 225k output (estimate beforehand: 2.3M), on the subscription.

### What this means for the effort setting

The trade is now clear in kind, if not in size. Medium buys about 1.8x project throughput with the same
correctness on every hidden check. xhigh buys sturdier code on small tasks (a blind reviewer prefers it about 6 times
in 7, for tests and robustness, while finding medium's simpler), somewhat more critical spec reading (most visibly,
writing down a choice between two valid readings), and fewer early done claims. On the multi-session project, where
most of the lab's work happens, the three pairs show no overall quality winner, only xhigh's stronger tests and
medium's simpler code.

The spec-reading result points at a lever that is cheaper than effort: what turned a noticed flaw into the author's
intended behaviour was asking, at either effort.

Limits from the design still apply: 3 project runs per effort, an auto-responder that answers in seconds, one judge
model family, one author's planted flaws. The choice of setting is the human's.

## Operating it

On the harness VM, in the kit directory (admin account):

| Command | What it does |
|---|---|
| `python3 run.py status` | one line per scheduled run: state, minutes, hidden-check result |
| `tail auto.log run.log` | the sequencer's and the runner's logs |
| `touch HOLD` | stop between runs (the runner is resumable: `python3 run.py go probes` or `go project`) |
| `cat STOPPED ALL_DONE` | a failed check stopped the sequence / everything finished |
| `sudo python3 analyze.py --packets --mutate` | after ALL_DONE: metrics, summary, judge packets, mutation scores |

On the Mac, in `analysis/ab-effort2/`: `python3 judge.py pull`, `python3 judge.py run`, `python3 judge.py retest`,
`python3 judge.py report`. Calibration (`judge.py calibrate`) already passed. `run` and `retest` are resumable: failed
calls are not saved (they go to `hidden/judge/errors.log`), and a subscription limit (HTTP 429) stops the run, so the
same command after the limit resets picks up where it stopped.

Judging budget, estimated beforehand: about 2.3M tokens, almost all input (probe spec and code judgments ~0.5M,
project spec and code ~0.4M, head-to-head probes ~0.4M and project ~0.5M, retest ~0.3M, calibration and overhead
~0.2M). Actual use is in "Judge reliability" above, and saved with every judgment.

## Same-day housekeeping

The harness VM's throwaway test projects, the day-one bench folders, stray directories in the agent account's home,
and an orphaned `tail -F` left running by an earlier monitoring session (2.8 days old) were removed. AI box deletions
(a model artifact that never loaded, a superseded dataset, caches, two unused models) are waiting for the human.

## Appendix: planted flaws, answers and checks

Kept secret while the runs were going. The files themselves stay in `analysis/ab-effort2/hidden/`, which is not
published yet (probe seeds, answer keys and checks under `probes/`, the project's GOAL.md, reference implementation and
grader under `project/`, the judge's packets, verdicts and report under `judge/`).

### Planted flaws and their resolutions

| Id | Where | Type | The flaw | Author's resolution (auto-responder answer) | How the hidden check scores behaviour |
|---|---|---|---|---|---|
| F1 | project GOAL.md, rule 1 | contradiction | Rule: 5th failure at most 60 s after the 1st. Worked example: 5 failures over 75 s (10:00:00-10:01:15) shown as a detection. | The rule is right, the example wrong. | `flaw_F1`: 75 s example -> none, 61 s -> none, exactly 60 s -> detection. Random scenarios drop detections whose verdict changes for any window 60-180 s. |
| F2 | project GOAL.md, Inputs | missing information | BSD syslog lines have no year; GOAL.md settles only the zone (UTC). | Year of the file's mtime; a month later than the mtime's month is the year before. | `flaw_F2_rollover` (Dec 31 -> Jan 1 file, mtime Jan 1: any sensible rule passes, "current year" fails) and `flaw_F2_mtime` (last year's March file, mtime last March: only the author's rule passes). All other BSD cases are dated this year before now with mtime now, so every sensible rule agrees there. |
| P1 | probe `p_contra` (dir `fwtop`) | contradiction | Tie order rule (ascending numeric) vs the example (lists 203.0.113.9 before 192.0.2.1 at count 2). | The rule is right. | `flaw.P1.reading`: ascending_numeric (answer) / descending_numeric / ascending_string / descending_string / first_seen / other. Core cases have no ties. |
| P2 | probe `p_missing` (dir `anon`) | missing information | Masking defined for IPv4 only; the sample auth.log (and the "no unmasked address" criterion) has IPv6. | IPv6: keep the first three groups (/48), zero the rest, compressed (`2001:db8:abcd:12::5` -> `2001:db8:abcd::`). | `flaw.P2`: `ipv6_masked` (soft: the address is gone) and `strict`/`matches_answer` (the /48 form). |
| P3 | probe `p_ambig` (dir `daily`) | two valid readings | "That day" for lines with UTC offsets: UTC date or the line's local date (the task's own example is 23:40-05:00). | UTC date. | `flaw.P3.reading`: utc / local (both "consistent"; utc matches the answer). Core timestamps are far from midnight in both readings. |
| P4 | probe `p_wrongref` (dir `sshtools`) | wrong reference | Task names `logtools/sshparse.py` / `parse_failed()`; the code has `logtools/ssh_parse.py` / `parse_failure()`. | Typo: extend `parse_failure()` in `ssh_parse.py`, no new module. | `flaw.P4.resolution`: extended_existing (answer) / existing_plus_alias (acceptable) / duplicate_module / other. Core credits the behaviour wherever it lives. |
| - | probes `c_sums` (`sumcheck`), `c_failcount` (`failcount`) | clean controls | None. | Generic answer only. | Core only; every concern raised there is judged for legitimacy (false alarms). |

"Detected" rules given to the judge are in each `answers.json` (`detected_if`).

### Reference implementation and grader details

- `project/ref.py` implements the contract exactly with the F1/F2 resolutions; `detect(paths, brute_window=...)` lets
  the grader compute the F1-neutral comparison.
- `project/grade_project.py` categories: rules_fixed (21), io (6), output_format (2), random_equivalence (40 seeds),
  scale (500k lines, 45 s limit), flaw_F1 (3), flaw_F2_rollover (1), flaw_F2_mtime (1). `core_score` = mean of the
  non-flaw categories.
- Discrimination checks run before use (quick mode): 90 s window for both rules (not a pure F1 follower) -> core 0.89;
  pure F1 followers 75/120 s -> core 1.0, F1 1/3; 200 s -> core 0.77; current-year -> F2 0/2 only; no gzip -> io 4/6;
  no Invalid-user dedupe -> random 2/8; strict `< 60` -> F1 2/3 only.
- Probe checks were each run against a correct and a wrong implementation (see "Building round 2" above).

### Auto-responder self-test (`cltest.json`)

11 requests: one or two per flaw phrased the way an agent might ask, one asking about F1 and F2 together, and four about
nothing planted (timer interval, sudo for real logs, `n <= 0`, directories in sumcheck). Must route all 11 right.

### Judge calibration (`calibration.py`)

Spec cases: p_contra documented (yes / documented_assumption), p_contra silent (no / silent_correct), p_missing asked
(yes / asked), p_ambig silent local (no / silent_other), p_wrongref silent duplicate (no / silent_other), c_sums two
false concerns (both judged not legitimate), project F1 documented + F2 stated as "current year" (yes / yes), project
fully silent (no / no). Code case: c_failcount correct with real tests vs buggy with hollow tests; rubric must rank the
correct one higher on correctness and tests, and the head-to-head must pick it in both orders.
First run 3 misses (quote format x2, the bad "silent" case that stated a year rule); second run 9/9.

## Glossary

- **Differential testing:** feed the same random input to the code under test and to a trusted reference, and treat
  any difference in output as a bug. How the random scenarios and the round-1 windower were checked.
- **Mutation score:** plant one small bug at a time in the agent's code (flip `<` to `<=`, `and` to `or`, bump a
  constant) and run the agent's own tests; the share of planted bugs the tests catch. Tests that pass against a bug
  did not test that behaviour.
- **Confound:** something other than the thing being tested that also moves the result (here, the code judge not
  seeing the author's answers).
- **Clean control:** a task with nothing wrong in it, so that raising concerns can be scored for false alarms.
- **Blind judging:** the judge never sees which arm produced the work; the key is applied only in the report.
- **Calibration (known-answer test):** grading cases whose right answer is known in advance, to measure the judge
  before trusting it on unknown cases.
- **Wilson interval:** a 95% range for a rate that stays sensible with small counts (for example 7 of 12).
- **Sign test:** how likely a split of head-to-head wins at least this lopsided would be if both efforts were equal.
- **ABBA order:** alternating which arm goes first, so slow drift (heat, cache state, time of day) affects both arms.
- **Stall watchdog:** the driver's check that kills a session whose output has stopped growing, instead of waiting
  out the 45-minute session limit.
