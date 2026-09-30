# Local Coding Agent Harness - Architecture and Decisions

The current state of the local autonomous coding agent: how it is built, why each part is the way it is, what was measured, what is being tested, and what is planned. It continues [Local Autonomous Coding Agent Build (first build, 2026-09-25)](history/first-build-2026-09-25.md) (the first build session, 2026-09-25), which is now partly out of date. Internal addresses, VLAN numbers, account names and key material are left out on purpose.

This note is the reference for starting a new Claude Code chat about the agent: read it first. The hardware, model serving, network and lab-wide changelog are in [AI Lab](ai-lab.md); the big picture (cloud architect, orchestrator script, per-task local agents) is in [Architecture](architecture.md).

## What changed since the first build

- GPU: RTX 4080 SUPER replaced by an RTX 3090 Ti 24 GB (a second 3090 Ti and an RTX 5060 Ti 16 GB are coming).
- Model server: llama.cpp replaced by vLLM (the HyperQwen build), about 100 tok/s instead of ~42, 150k context.
- Harness: many additions to the Pi-based loop (below). The biggest change in approach: the harness is designed around how the agent actually behaves, measured from its session logs, instead of instructions that try to make it behave.

## Architecture

| Part | What it is | Notes |
|---|---|---|
| AI box | i9-14900KF, RTX 3090 Ti 24 GB, Ubuntu, headless | CPU power-limited to 125 W (RAPL PL1/PL2), GPU capped at 350 W, both applied at boot before the model server starts. Temperature log every 15 s with an automatic safety stop of the model server on sustained overheating. |
| Model | Qwen3.8-27B, 4-bit (W4A16 AutoRound) | Hybrid: most layers are linear attention (DeltaNet), 16 are full attention, so context memory grows slowly. 4-bit costs about 1.3% perplexity against 8-bit. |
| Model server | vLLM with the HyperQwen patches, in Docker, bound to localhost only | MTP speculative decoding (3 draft tokens, 69% accepted, about 3.1 tokens per step), prefix caching (94% of prompt tokens served from cache), vision enabled, 150k context, 200k tokens of context memory (KV cache) in total. |
| Harness VM | Ubuntu VM in an untrusted, isolated VLAN | Reaches the model server only through an SSH tunnel whose key can forward exactly one port. The agent's own machine: it may install packages and research the web. |
| Agent harness | Pi coding agent 0.87.1 | Extensions add the hand-over, the status footer, the thinking budget and the web tools. Thinking effort: xhigh (fixed, see decisions). |
| Loop driver | `agent-loop`, plain bash, no model | Picks tasks, launches one fresh Pi session per step, verifies done claims, archives the journal, records every session in a ledger. |
| Viewer | Pi's own terminal UI in a tmux window | Context use, speed (from the model server's metrics), iteration, task, elapsed time in the footer. |

## Design principles

1. **Design around how the agent behaves, not how we wish it behaved.** Before adding a rule, check the session logs for what the agent actually does. Measured on the hollowdeep project: every rule enforced in code held (hand-over, tool gate, archiving); every rule that existed only as prose in AGENTS.md failed (entry length limits, targeted reading, "stop after one step", archive use). Give the agent permissions and places to put things; put the constraints that matter into the harness.
2. **Supervision lives outside the model.** Loop detection, done-verification and hand-over limits are enforced by the driver and the extensions. A model that has lost track cannot be relied on to notice it.
3. **The human owns WHAT, the agent owns HOW.** Task files present when a loop starts are the human's: their acceptance boxes define done and only the human changes them. The agent may split any task into subtasks, add tasks for work it discovers (bugs, tooling, refactors, features), and propose changes to the human's criteria. This also lets it fill the gaps no prompt can cover.
4. **Claude is the architect, not the orchestrator.** Claude turns the operator's intent into design docs, seed tasks and harness rules and fixes foundational harness bugs; it does not re-plan or rewrite the agent's tasks. Monitoring reports exceptions only.
5. **Measure before and after every change.** Every session is in the ledger with the exact harness configuration it ran under, so any change can be judged on real numbers.

## The loop, session by session

1. The driver waits until the model server answers (an outage does not burn sessions), picks the next task, regenerates CODEMAP.md from the code, and records which tests are already failing when a task is first handed out.
2. A fresh Pi session starts with a one-paragraph prompt (iteration, task file, plus nudges: split a task that has taken 5+ sessions, read DECISIONS.md to the end when it is large).
3. The agent orients (memory files, task file, git log, the code it needs), does one step, runs the tests, writes its notes, commits, and stops.
4. The hand-over extension watches context use: at 120k tokens it tells the agent to hand over (only memory files, task files and git work from then on, thinking capped at 2k per turn); at 142k or after 10 more turns it ends the session. Compaction is always cancelled. Near the 150k window it counts each prompt exactly with the server's tokenizer and shrinks the requested output so no request can overflow.
5. The driver saves any uncommitted code, verifies a done claim, archives finished tasks' journal entries, audits the task queue, and writes the ledger line.

### Memory files

| File | Holds |
|---|---|
| `PROGRESS.md` | Snapshot of now: current task, state, exact next step (being A/B tested, see below) |
| `DECISIONS.md` | The agent's journal: choices, failed attempts and why, blockers, PITFALLs (facts that stay true). Finished tasks' entries move verbatim to `DECISIONS-archive.md`; an index lists one line per archived task |
| `CODEMAP.md` | Generated before every session from each file's header comment and exports, with line ranges for functions in big files. Architecture rules above the marker line are hand-written |
| `tasks/*.md` | The queue. Subtasks are `025a-...`, `025b-...`; a task with unfinished subtasks waits for them, then comes back for its own boxes |
| git log | What was done, one commit per step |

### Verification

- Every done claim is re-checked by the driver: all boxes ticked, and the test suite run by the driver twice (a failure that disappears on rerun is logged as flaky, not blamed on the task).
- The human's tasks need a fully green suite. The agent's own tasks may finish with tests that were already failing when the task was first handed out, never with new failures. This lets a subtask that fixes part of a sibling's broken tests be accepted.
- If the agent removes one of the human's acceptance boxes, the claim is rejected and flagged. A `## Proposed changes` section is the legitimate way to say a criterion is wrong.
- Driver notes in DECISIONS.md stay short (first 8 failing tests plus a pointer to the full list in `.agent/reports/`).
- A task still unfinished after 8 sessions is logged as STALLED (and every 4 sessions after).

### Hangs (since 2026-09-30)

A command that never returns used to cost a whole session: Pi's bash tool has no default timeout, so a hung test
sat until the driver's 45-minute limit, and the next session ran it again (hollowdeep: 11 of 216 sessions ended on
that limit; the effort A/B lost two sessions in a row this way). Three layers now stop that:

1. **Per command** (`bash-timeout.ts`): every bash call without its own timeout gets 600 s. Pi kills the command's
   process tree and returns the output so far with "Command timed out after 600 seconds", so the agent sees where it
   hung and fixes it in the same session. A call can ask for longer.
2. **Per session** (driver watchdog): a session whose output has not grown for 15 minutes (`STALL_KILL`) is stopped,
   together with every process it started (Pi runs commands detached, in their own process groups). A running call
   that asked for a longer timeout gets that long plus a minute. The driver writes a note into the task's hand-over
   naming the command that hung and how to find the hang, so the next session starts there.
3. **Driver test runs**: capped at 15 minutes, and a suite that timed out is reported as a hang instead of being
   rerun as a possible flake.

### Tools the agent has

- Read, edit, write, bash (Pi built-ins).
- `web_search` (a self-hosted SearXNG on the VM) and `web_fetch` (pages turned into text; long pages come back as a verbatim extract for a question, the full text saved to a file to grep). Results are labelled untrusted.
- `sudo apt-get` only (root-equivalent on its own VM; new system packages must be recorded in the journal so the machine can be rebuilt).
- A browser screenshot tool for the game.
- `ask_human`: a request to the human for what only a person can do (hardware, accounts or credentials, anything outside the VM, a decision the acceptance criteria do not settle). See below.

### Starting from GOAL.md

A project can be a folder with nothing but a `GOAL.md`. The driver copies in the template files and creates the git repository, then writes task 000: turn `GOAL.md` into `PLAN.md` (approach, architecture, tech choices with reasons, assumptions, out of scope, and which task delivers each point of the goal) and a task queue with testable acceptance criteria. The driver accepts the plan only if `PLAN.md` and at least one task exist and the test command works. From then on the agent owns the plan and its tasks; `GOAL.md` stays the human's.

- **Goal changed:** when `GOAL.md` differs from the version last planned against, the driver reopens task 000 with the diff (before the next session starts), so the agent updates the plan and the queue first.
- **Goal check:** an empty queue does not end the loop. Task 999 checks the project against `GOAL.md` point by point, with evidence, into `GOAL-CHECK.md`, and adds tasks for anything missing. The loop stops after a check that adds nothing, or after `GOAL_CHECKS` (3) rounds for the same goal.
- **Test command** is re-checked before every test run (planning may add a `package.json`).
- 000 and 999 are written by the driver and registered as the human's, so their acceptance boxes cannot be changed by the agent.

### The human talking to the agent

What the human types in `agent-watch` goes to `.agent/inbox/`. During a session the `human-messages` extension delivers each message as a Pi steering message: the model gets it after its current turn, when the response being generated and its tool calls have finished (measured: a message sent 8 s into a long thought arrived 143 s later, after ~11k thinking tokens and one tool call; the model then followed it). Esc asks for a stop instead: a headless Pi cannot take a message after an abort (sending one throws), so the extension aborts the session and leaves the message for the driver, which opens the next session's prompt with it, as it does for anything typed while no session runs. The ledger records `human_messages` and `human_interrupt` per session.

### Asking the human

The agent files a request with `ask_human`; the human reads it in the telecloak desktop app (it arrives on the phone as encrypted Telegram text) and answers there, or on the VM with `agent-talk`.

- **The request is filed in the project** as `.agent/asks/<n>.md` (terminal control characters and bidi overrides stripped, since the human may read it in a terminal), and its text goes to the human through `ring-doorbell --ask <n>`.
- **The agent never touches crypto (telecloak).** The VM holds neither the bot token nor the key: it hands the text to the AI box over SSH with a key whose forced command is the relay (`server/doorbell/`), from the VM's address only, no PTY, no forwarding. The relay encrypts it (AES-256-GCM with a pre-shared key, a separate key per direction) and sends it; Telegram sees only `tc1.` ciphertext. Without a key on the AI box only the fixed "help i need your attention" leaves. Rate-limited on the AI box (20 messages an hour, 100 a day; a reminder ping within 10 min of the last is not re-sent).
- **The human's side comes back the same way.** The `telecloak-pull` service on the VM long-polls the relay; the AI box accepts only messages from the human's own account that decrypt with the human-to-bot key, are at most a day old and not replays. An **answer** marks the request answered exactly as `agent-talk` does; a **message** goes to `.agent/inbox/` like typing in `agent-watch`; **start / stop / stop now / status** run `agent-start`, `agent-stop` and a short status. Each result is sent back to the app.
- **What this gives up:** before telecloak the agent could only ring a fixed bell; now it chooses the words the human reads, so a prompt-injected agent could show a convincing malicious command. The app shows plain text only (no links, control and bidi characters stripped); the rule below matters more than before.
- **Blocking means the task waits, not the loop** (changed 2026-09-29 after a night where the loop sat idle, then exited, while other work was possible). A blocking request makes only its own task wait: the loop works on other tasks meanwhile (the prompt lists the waiting ones) and comes back to that task first once it is answered. Tasks the agent sets to `blocked` while a request is open are recorded as waiting on it and resume with it. Only when nothing else can be worked on does the loop wait; it never exits with a request open, a message typed in `agent-watch` also ends the wait, and the doorbell rings when a wait starts and every `ASK_REMIND_HOURS` (6) after.
- **Answering.** `agent-talk` (no argument: every project with open requests) shows each request and offers: talk (a live Pi session in the project with a fresh agent that reads the request first; while it runs `.agent/TALK` holds the loop, and a running session is waited for), a short typed reply, or skip. Answering marks the request `answered`.
- **Delivery.** An answered request's task goes next; its session's prompt points at the answer, then the driver marks the request `closed`, so an answer reaches exactly one session. A task that was `blocked` or already `done` is set back to `in-progress` for it. The ledger counts `asks_filed` per session.
- **Tested end to end (2026-09-28)** on a throwaway task only the human could finish: the agent asked unprompted after 80 s, wrote the wait into its hand-over and kept the status, the loop held, released within a minute of the reply, and the next session finished and was verified.
- **Treat requests as untrusted input.** They come from a model that reads web pages; read any command in one before running it.

## Measurements that drove the decisions (hollowdeep, a browser game built by the agent)

- **Where time goes:** 98% of session wall time is the model generating; tests are 2% (median test run 1 s). 72% of generated tokens are thinking.
- **"Orientation":** about half of each session (~5 of ~9 minutes) passes before the first code edit. Reading memory files and code is cheap (served from cache); the time is one or two long planning turns in which the model drafts every edit word for word in its thinking, then writes it again as tool calls.
- **Task size was the biggest problem:** three large tasks took 26, 32 and 26 sessions with 70-80% of sessions cut off at the context limit. After the agent was allowed to split tasks, the rest of task 025 took 9 sessions instead of the 24 already spent.
- **Memory bloat:** DECISIONS.md was the largest single read (about 12.7k tokens per session, more than all source code combined) and grew past the 50 KB a single read returns, so agents missed the newest entries. Fixed by the per-task archive, the one-line index and smaller tasks.
- **Hand-over size:** median 11.4k tokens from the hand-over message to the end of the session, 90th percentile 20.4k, maximum 27.9k. That is why the hand-over now starts at 120k instead of 100k.
- **Context depth:** tool errors rise from 0.4% (first 40k) to about 3-4% (40-100k) and 6.5% beyond 100k (the last number is inflated by hand-over edits of big files).
- **Concurrency:** one agent uses about half the GPU. A second concurrent request raised combined generation from 78 to 192 tok/s in a probe. The limit is context memory (200k tokens in total), not compute.
- **Second GPU (from the HyperQwen project's own tests on two 3090s):** splitting one model across both cards speeds a single agent up only 16-35%; two separate copies give about twice the total work.
- **Agent honesty:** one rejected done claim in about 180 sessions.
- **The game after ~40 hours of agent time:** 7,368 lines of code, 12,221 lines of tests (405 tests), about 300 commits, three design versions.

## Assessment of the model (Claude's view, 2026-09-28)

A very good executor and a weak engineer-in-charge. Its code is careful and correct (clean pure modules, edge cases handled, tests for everything, honest about done). Where it falls short is judgment: tests that pin exact constants (so every design change breaks many tests), comments that narrate task history instead of intent, no instinct to refactor (a 1,400-line file grew unchallenged), big-bang migrations instead of incremental ones, and it never pushes back on a spec. Its logs show it notices problems outside its task and deliberately leaves them ("scope creep"), which is why AGENTS.md now tells it to turn such observations into new tasks. Estimated: a job it needed ~35-40 hours for would take Claude about one working day interactively; the local model does it unattended for a few dollars of electricity. Vendor benchmarks put it near Claude Opus 4.5-4.6; on a long project the gap is in judgment and efficiency, not per-edit correctness.

## Decisions (with the reason)

| Date | Decision | Why |
|---|---|---|
| 09-26 | Ledger with a harness stamp per session | Attribute every outcome to an exact configuration |
| 09-26 | Journal archive with an index; compaction always cancelled | A compacted session carried on with a blurred memory of its own work |
| 09-27 | vLLM HyperQwen over llama.cpp and NInfer | About 100 tok/s, 150k context, quality within ~1.3% of 8-bit |
| 09-27 | Agent may install packages and research the web | Its own machine in an isolated VLAN; it was already reaching the network on its own |
| 09-27 | Agile tasks (split, add, propose; human criteria fixed) | Fixed tasks locked the agent into waterfall; its own plans had nowhere to live |
| 09-27 | CODEMAP generated from the code | Agents read the detail files in only 25% of sessions but spent effort maintaining them |
| 09-28 | Hand-over at 120k instead of 100k, output clamped at the window edge | The hand-over needs ~20k at the 90th percentile; 100k wasted a third of the window |
| 09-28 | Per-task test baselines | A subtask fixing part of a sibling's broken tests was rejected for the sibling's failures |
| 09-28 | Driver waits for the model server; short driver notes; STALLED flag; `agent-report` | General robustness and a standard before/after report |
| 09-28 | "Check facts by running code instead of reasoning about them" | The model spent thousands of thinking tokens on arithmetic a one-line script answers |
| 09-28 | "Things noticed outside the task become new tasks" | Its logs show it notices problems and leaves them as "out of scope" |
| 09-28 | Keep only the 6 newest images in each model request (`image-budget.ts`); log sessions that end on a server error | The server accepts at most 8 images per request and every earlier turn is resent: sessions that checked their work with 9+ screenshots died on HTTP 400 with no hand-over (found by the hand-over A/B) |
| 09-30 | Default command timeout, session stall watchdog, no rerun of a timed-out test suite | A hung test cost 45 minutes per session, repeatedly (see Hangs) |

**Decided against (do not re-propose):** lowering thinking effort from xhigh (reopened by the human on 2026-09-29
for measurement: `analysis/ab-effort/`, `analysis/ab-effort2/`); lowering the 16k per-turn thinking cap (it fires in ~9% of sessions and the model recovers well); a same-model reviewer agent (it shares the model's blind spots, and done claims are already honest); RAG over the code; Codex as the harness (20k+ tokens of built-in prompt); a higher-precision quant or bigger context for their own sake; locking down the VM's internet access.

## A/B test: hand-over notes in the task file

**Question:** should hand-over notes move from the single `PROGRESS.md` snapshot into a `## Hand-over` section of the task being worked on? This is needed before two agents work on one project, and it has to be something the agent follows reliably.

**Why the change is expected to help:**
1. No merge conflicts between two agents: every session rewrites PROGRESS.md completely, so two agents on two branches would conflict on every merge; notes in each agent's own task file never collide.
2. Notes stay with the work: today a parent task's hand-over is overwritten by its subtasks' sessions; per-task notes survive until the parent comes back.
3. Less to read, a cleaner record (archived with the task), and it scales to any number of agents.

**Risk:** the agent rewrites PROGRESS.md in 94% of sessions by habit. So in the new design PROGRESS.md becomes a driver-generated overview, and a safety net moves anything the agent still writes there into the task file (each time counts as a miss).

**Part 1 (structural, no model):** simulate two agents on two branches in a scratch repository, once with today's files and once with the new design, and check which merges cleanly and whether notes are lost.

**Part 2 (model A/B):** replay task 025 from the commit right after the agent split it (subtasks 025a and 025b, then the return to the parent), on a copy of the project so the live repository is untouched, with the live loop paused and production limits.

- Arm A: today's harness. Arm B: notes in the task file's `## Hand-over` section (a subtask also reads its parent's), generated PROGRESS.md overview, safety net, matching AGENTS.md, prompt and hand-over message.
- Measured per arm: compliance (share of sessions whose notes landed in the task file without the safety net), resume quality (does the next session's first real action follow the previous "exact next step": follows / partly / ignores or redoes, graded with the arm labels removed), whether the returning parent task still has usable notes, context and time to the first edit, subtasks accepted, rejections.
- About 10 sessions per arm: enough to see a clear compliance problem or a clear difference, not small effects.

Run 1 (2026-09-28) was stopped and discarded after arm A's sessions on 025b started dying on the image limit above (a harness bug, not a hand-over effect). Run 2 ran each arm twice (A, A2, B, B2) to measure run-to-run noise, 12 sessions at most per run.

**Results (run 2):**

| Run | Notes in | Sessions to finish 025 (split to parent accepted) | Hours | Context at first code edit | Edit failures |
|---|---|---|---|---|---|
| A | PROGRESS.md | 10 (one done claim rejected for unticked boxes) | 1.06 | 64k | 13% |
| A2 | PROGRESS.md | 11 (split the parent once more before closing it) | 1.16 | 59k | 0% |
| B | task file | 10 | 1.17 | 68k | 14% |
| B2 | task file | 8 | 1.00 | 68k | 6% |

- **Compliance:** in the task-file runs the notes landed in the task file in 21 of 21 sessions, 20 of them without touching PROGRESS.md; the safety net was needed once.
- **Performance:** identical within noise. Two identical runs differ by 1-2 sessions and ~0.1-0.17 h; the difference between the designs is smaller than that.
- **Returning to the parent:** every run closed the parent task in one session. In the task-file runs the parent's notes were the plan written when it was split; one agent noticed they were dated and checked `git log`, as intended.
- **Resume behaviour:** sessions in both designs opened by stating the step from the notes, and no run redid finished work. (The resume-quality grading could not be made fully blind in the first pass because file paths revealed the arm; the behaviour showed no difference between designs either way.)
- **Conclusion:** hand-over notes in the task file are followed reliably and cost nothing, and they merge cleanly for two agents (Part 1). Adopt them for the two-agent setup.

## Harness bake-off: Pi vs Oh My Pi (2026-09-28)

Oh My Pi v18.3.3 (a Pi fork with hash-anchored edits and language-server tools) ran the same replay with the lab's extensions loaded unchanged, notes in PROGRESS.md, compared with runs A and A2.

| | Pi (A, A2) | Oh My Pi (O) |
|---|---|---|
| Sessions / hours to finish 025 | 10-11 / 1.06-1.16 | 12 / 1.87 |
| First prompt (system + tools) | ~3k tokens | ~9k tokens |
| Context at first code edit | 59-64k | 81k |
| Sessions cut off by the context limit | 1 of 12 | 4 of 12 |
| Thinking tokens (12 sessions) | 204-219k | 360k |
| Edit failures | 0-13% | 11% |
| Tool calls | ~110 reads, ~190 bash | 195 reads, 45 grep, 118 bash |

Oh My Pi finished the task but ~70% slower: a larger built-in prompt, more reading before acting, more thinking, and more sessions hitting the context limit. Its hash-anchored edits did not reduce edit failures for this model. Decision: stay on Pi (one run, but the gap is far outside the run-to-run noise).

## Measured: two agents on one GPU (2026-09-28)

Two agents ran the same replay (task 025) at the same time on the one RTX 3090 Ti, production settings, task-file notes, while the server's metrics were sampled every 30 s.

| | One agent (4 earlier runs) | Two agents at once |
|---|---|---|
| Time to finish 025 | 1.00-1.17 h each | ~2.0 h each (2 tasks in 2.07 h) |
| Throughput | 1x | **~1.06x** |
| Combined output while both generate | ~80-100 tok/s | 91 tok/s |
| Context-memory preemptions | - | 243 in 2 h; a request waiting in 26% of samples |

- **Speculative decoding (MTP) already uses the GPU's spare capacity**, so a second concurrent agent adds almost nothing (HyperQwen's batch mode only beats MTP from ~8 concurrent requests up), and the 200k-token context pool (already 8-bit) is too small for two agents at production limits.
- An earlier probe (78 -> 192 tok/s with a second request) was unrepresentative: short, thinking-off generation with no memory pressure.
- **Conclusion: one agent per GPU.** More agents need more cards; two per card is not worth it with this model and config.
- The first attempt at this test exposed a driver bug that would also have broken the two-agent design: the TUI done-signal (`tmux wait-for agent-done-N`) was shared between loops on the same iteration number, so when either agent finished, both sessions ended. Fixed: the signal is per project.

## Experiment: thinking effort on the real workflow (A/B v2, running from 2026-09-30)

The first effort A/B (`analysis/ab-effort/`) measured one coding task with one hidden grader, and that grader turned
out to score a spec contradiction instead of the code: the arms that noticed the contradiction were marked as
failures. Re-graded with the end-of-stream flush moved late enough for either reading (2026-09-30): all three xhigh
arms and two of three medium arms produce exactly canon's windows (the third medium arm has a real windowing bug,
279/300), and only two xhigh arms handle an event that arrives late into a short window (the spec's flush rule and its
"exactly canon" rule disagree there, and those arms chose canon). Medium used fewer tokens (27-44k vs 47-85k), and
one xhigh arm lost 90 minutes to a test that hung (a harness gap, fixed since). Three runs per arm decide nothing.
It also only asked "can it write this function". The human uses the model as a multi-day project worker,
so v2 (`analysis/ab-effort2/`) measures the parts of that job separately, at `THINKING=xhigh` vs `medium`, with
production settings otherwise:

| What | How it is measured |
|---|---|
| Spec reading | Six one-task probes: four specs with one planted flaw each (a contradiction, missing information, two valid readings, a wrong file/function name) and two clean controls. Two more flaws are planted in the project's GOAL.md. A blind Claude judge grades whether the agent noticed each flaw and how it handled it (asked, documented an assumption, proposed a change, silent), and whether its other concerns were legitimate. The controls give the false-alarm rate. |
| Correctness | Hidden checks per probe and a hidden end-to-end suite for the project (fixed cases, 40 random scenarios against a reference implementation, gzip/multi-file/garbage input, output format, a 500k-line timing case). Each planted flaw is scored in its own category, and any behaviour that depends on how a flaw was resolved is removed from the core categories, so resolving a flaw one way never changes the core score (the v1 lesson). |
| Code quality | Blind Claude judge (rubric 1-5 on correctness risk, readability, design, tests, robustness, simplicity; project runs also pairwise, both orders), mutation score of the agent's own tests (small bugs planted one at a time: how many its tests catch), ruff issues per 100 lines. |
| Multi-step project work | A security CLI (SSH brute force / spraying / login after failures, firewall port scans; JSON-lines detections) built from a GOAL.md through the whole loop: planning, tasks, goal checks. Wall time, sessions, tokens and thinking share, rejected done claims, stalls, tasks the agent added, requests to the human. |

Design details: 4 repetitions per probe per arm and 3 project runs per arm, arms alternated (ABBA) against drift;
each run starts only after the model server has been idle for a minute, and vLLM load is sampled every 20 s so any
overlap with other work shows up. Requests to the human are answered by an auto-responder from the run's answer key
(the local model, thinking off, classifies which planted flaw a request is about; anything else gets "choose, write
the assumption down"); the doorbell is faked so no messages go out. The judge is headless `claude -p` with its own
system prompt, no tools and no user settings, and sees anonymous packets with effort labels scrubbed. Answer keys,
hidden checks and the project GOAL.md stay unpublished (`analysis/ab-effort2/hidden/`, gitignored) until the runs are
finished, because the agent can search the web. The full process (round-1 audit, design, build log, GPU hand-over,
operating commands) and, later, the results: [effort-ab.md](effort-ab.md).

## Planned: two agents on one project (when a second 24 GB GPU arrives)

- One model copy per card (ports 8080 and 8081), 4-bit, MTP, 150k window each. First a ~30 minute A/B: two separate servers with two agents, one split server with two agents, one split server with one agent.
- Each agent works in its own git worktree and branch; `main` is where finished work comes together, and each session starts by merging the latest `main`.
- One scheduler hands each task to at most one agent, and only when its dependencies are done. Subtasks get a `Depends on:` line from the agent that splits them (missing means "after the previous sibling").
- On acceptance the driver merges `main` into the branch, re-runs the tests, then merges to `main`; a conflict goes back to the same agent.
- Memory for two writers: hand-over notes in task files (if the A/B confirms), DECISIONS.md merged with git's union strategy (both sides' appended entries kept), CODEMAP and the archive regenerated after each merge.
- Parallelism depends on declared dependencies: the current human task chain (027 to 033) is strictly sequential, so seed tasks should list only real dependencies.

## Do-later list

- When the second 3090 Ti arrives: rerun the quantization comparison as a **capability** test. The 2026-09-27 sweep measured only how closely each quant's predictions match 8-bit (KL divergence, perplexity) and speed; those are proxies. With 48 GB the 8-bit model fits entirely on GPU, so run the same agentic tasks with hidden tests under Q4, Q6 and Q8 and compare task success, sessions per task, rejected claims and tool errors.

- Kickoff flow for new projects: from a one-line idea the agent writes the design doc and proposes the task queue; the operator approves the acceptance criteria.
- Harness bake-off on replayed tasks with hidden tests: Pi vs Oh My Pi (hash-anchored edits, language-server tools), possibly others.
- `sim_view` tool: run the game simulation for N ticks from a seed and return an image (map, paths, entities), so the agent can see emergent bugs. The model has vision; the agent almost never uses it.
- Best-of-2 attempts on stuck subtasks in separate worktrees, the tests pick the winner (fits the second GPU).
- Experiment: drop old thinking from context (keep the last few turns); test parsers for jest, vitest, cargo and go when a non-JS/Python project starts.

## Operating it (commands on the harness VM)

| Task | How |
|---|---|
| Start or resume the loop | `agent-start <project>` (name or path): runs in the background, no tmux, survives logout; refuses when no task's first line is `Status: open` |
| Watch it live, and talk to it | `agent-watch <project>`: Claude Code-style view (edits as diffs, code highlighted, thinking dim, Ctrl-T hides it). Type a message and press Enter: the agent gets it after its current step. Esc with a message typed: stop the session now; the next one starts with the message. Ctrl-C leaves the view; the loop keeps going. `AGENT_WATCH_BG=dark` for a dark terminal |
| Stop it | `agent-stop <project>` (after the current session) or `agent-stop <project> --now` |
| See how it is doing | `agent-report <project>` (per task and per session, with the harness version) |
| New project | Make a folder in the projects directory, put a `GOAL.md` in it (what you want, in your own words), `agent-start <name>`. The agent plans it (`PLAN.md`, tasks), builds it, and checks the result against `GOAL.md` before the loop stops. Edit `GOAL.md` any time: the next session re-plans |
| Veto a task the agent added | Set its first line to `Status: dropped` |
| Change a criterion the agent proposed changing | Edit the task file yourself (only the human changes the human's criteria) |
| Answer the agent's requests | In the telecloak app (lab bot chat): the request is pre-selected under "Send as", type the answer, Cmd+Enter. Or on the VM: `agent-talk` (all projects) or `agent-talk <project>`: talk live with an agent about it, or type a short reply |
| Message, start, stop or check a project from anywhere | telecloak app, lab bot chat: pick the project, then type a message or press Start / Stop / Stop now / Status |
| Ring the doorbell by hand (test) | `ring-doorbell` in a project folder |
| Set up telecloak (once) | App: Add chat > the bot's @username > Generate new key > Copy key. Then `ssh -t <ai box> sudo telecloak-setup` and paste it; the app shows the test ping and the same key fingerprint |
| Where things are | Ledger `.agent/iterations.jsonl`, loop log `.agent/loop.log`, sessions `.agent/sessions/`, driver reports `.agent/reports/`, project template `~/.agent-kit/template` |

## Lessons added since the first write-up

1. **The agent's logs are the spec for the harness.** Every prose rule that failed and every mechanism that worked was visible in the session files before anyone argued about it.
2. **Ownership beats instructions.** Allowing the agent to split tasks fixed more than any rule about task size did.
3. **Verify remote state before reporting it.** An interrupted command had already done its work; the report said otherwise.
4. **Truncating thinking is fine as a safety net, harmful as a throttle.** A 16k cap that fires rarely is harmless; a 4k cap would break results.
5. **Hardware: the limit on parallel agents is context memory, not compute.** One agent leaves half the GPU idle.
