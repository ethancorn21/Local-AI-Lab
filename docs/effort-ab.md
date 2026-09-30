# Thinking effort A/B: does `xhigh` vs `medium` change how the lab works?

Status (2026-09-30 04:10 UTC): round 2 (v2) is built, tested without the model, and scheduled. It starts
unattended when the GPU frees up (about 04:45 UTC) and should finish around midday UTC on 2026-10-01; judging
follows. Results will be added to this file. The planted spec flaws and answer keys stay unpublished
(`analysis/ab-effort2/hidden/`) until the runs are over, because the agent under test can search the web.

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
  hidden is published until the runs are over (the agent has web search).
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
`python3 judge.py report`. Calibration (`judge.py calibrate`) already passed.

Judging budget, estimated: about 2.3M tokens, almost all input (probe spec and code judgments ~0.5M, project spec and
code ~0.4M, head-to-head probes ~0.4M and project ~0.5M, retest ~0.3M, calibration and overhead ~0.2M). Actual token
use is saved with every judgment.

## Same-day housekeeping

The harness VM's throwaway test projects, the day-one bench folders, stray directories in the agent account's home,
and an orphaned `tail -F` left running by an earlier monitoring session (2.8 days old) were removed. AI box deletions
(a model artifact that never loaded, a superseded dataset, caches, two unused models) are waiting for the human.

## Glossary

- **Differential testing:** feed the same random input to the code under test and to a trusted reference, and treat
  any difference in output as a bug. How the random scenarios and the round-1 windower were checked.
- **Mutation score:** plant one small bug at a time in the agent's code (flip `<` to `<=`, `and` to `or`, bump a
  constant) and run the agent's own tests; the share of planted bugs the tests catch. Tests that pass against a bug
  did not test that behaviour.
- **Clean control:** a task with nothing wrong in it, so that raising concerns can be scored for false alarms.
- **Blind judging:** the judge never sees which arm produced the work; the key is applied only in the report.
- **Calibration (known-answer test):** grading cases whose right answer is known in advance, to measure the judge
  before trusting it on unknown cases.
- **Wilson interval:** a 95% range for a rate that stays sensible with small counts (for example 7 of 12).
- **Sign test:** how likely a split of head-to-head wins at least this lopsided would be if both efforts were equal.
- **ABBA order:** alternating which arm goes first, so slow drift (heat, cache state, time of day) affects both arms.
- **Stall watchdog:** the driver's check that kills a session whose output has stopped growing, instead of waiting
  out the 45-minute session limit.
