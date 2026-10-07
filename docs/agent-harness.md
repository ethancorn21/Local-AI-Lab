# Agent harness: the reference

The precise rules of the coding-agent harness, each with the reason it exists. Read
[How the harness works](how-it-works.md) first: it explains the same system in plain language. Hardware and model
serving are in [ai-lab.md](ai-lab.md), measured comparisons in [experiments.md](experiments.md), and the stories behind
many of these rules in [history/](history/).

## Design principles

1. **Design around how the agent behaves, not how we wish it behaved.** Check the session logs before adding a rule.
   On the first long project, every rule enforced in code held and every rule written only as instructions failed. So
   the agent gets permissions and places to put things, and the constraints that matter live in the harness.
2. **Supervision lives outside the model.** Loop detection, done checks and context limits are enforced by the driver
   and the extensions. A model that has lost track cannot be relied on to notice.
3. **The human owns what; the agent owns how.** The human's acceptance criteria define done. The agent may split
   tasks, add tasks and propose changes to criteria, but never change the human's criteria itself.
4. **Claude is the architect, not the orchestrator.** It designs goals and the harness and fixes the harness when the
   data says so. It does not re-plan the agents' tasks while they work.
5. **Measure before and after every change.** Every session is recorded with the exact harness version it ran under.
6. **Fix the cause, so the agents can handle it themselves next time.** A problem a human had to clear by hand once
   becomes a harness change that lets agents clear it in every project.

## The loop

One session = one fresh agent doing one step of one task:

1. The driver waits until the model server answers, picks the task, regenerates `CODEMAP.md` and notes which tests
   already fail when a task is first handed out.
2. A fresh Pi session starts with a one-paragraph prompt: the task file, plus nudges (split this, your teammate is
   working on X, your request was answered).
3. The agent orients (task file, `PROGRESS.md`, the `PITFALLS.md` contents, `CODEMAP.md`, git log; other files are
   searched, not read whole), does one step, runs the tests, writes its hand-over notes, commits and stops.
4. The driver saves any uncommitted code, checks a done claim if there is one, archives finished journal entries and
   writes one line to the ledger.

### Limits

| Limit | Value | Why |
|---|---|---|
| One command | 600 s, then killed with the output so far | A hung test used to cost a whole session |
| Session without output | 15 min, then stopped with everything it started | Same |
| One session | 45 min | Bounds the damage of any session |
| Thinking per response | 16k tokens, ended with a wrap-up sentence | More bought nothing measurable, less cost correctness ([experiments](experiments.md#thinking-cap)) |
| Context: hand-over starts / session ends | 120k / 142k on the 24 GB cards; 75k / 100k on the 5060 Ti | A hand-over needs up to ~20k tokens; the small card's window is 114k |

At the hand-over limit the agent is told to write its notes; from then on only notes and git work, with thinking
capped at 2k per turn, and the session ends a few turns later. Context compaction is always cancelled. Near the
window's edge every request is counted exactly and its output shortened so it cannot overflow.

### Memory files

| File | Holds | Written by |
|---|---|---|
| Task file, `## Hand-over` | Where the task stands and the exact next step | The agent on that task |
| `PROGRESS.md` | Tasks in flight, and the current task's own journal entries | The driver, before every session |
| `DECISIONS.md` | Journal of choices, failed attempts and blockers. Finished tasks' entries move to `DECISIONS-archive.md` | Agents; archived by the driver |
| `PITFALLS.md` | Lasting facts (tool quirks, library traps, machine limits, traps in this code), each with where it bites and the symptom | Agents, curated |
| `CODEMAP.md` | What each file does and where its functions are | Generated from the code |
| `PLAN.md` | The approach, and which task delivers each point of `GOAL.md` | The planning agent |
| `tasks/*.md` | The queue; subtasks are `025a-...`, `025b-...` | Human and agents |

Why: an agent remembers nothing between sessions, so anything worth keeping is written down. Splitting lasting facts
(`PITFALLS.md`) from the journal cut what a session reads at start-up from 63k tokens to 14k on frontpage.

### Tools the agent has

- Read, edit, write and bash (Pi's built-ins).
- `web_search` (a self-hosted SearXNG) and `web_fetch` (pages as text, labelled untrusted).
- `sudo apt-get` only. Python packages go into a per-project virtualenv with every version and hash pinned; model
  weights only through the library's own download at a pinned revision with recorded checksums.
- A browser screenshot tool, and `ask_human` (below).

### Done checks

- The driver re-checks every done claim: every box ticked, and the test suite run twice (a failure that disappears on
  the rerun is logged as flaky, not blamed on the task).
- The human's tasks need a fully green suite. The agent's own tasks may finish with tests that were already failing
  when the task was first handed out, never with new failures.
- Removing one of the human's acceptance boxes gets the claim rejected. `## Proposed changes` is the way to say a
  criterion is wrong.
- A task unfinished after 8 sessions is flagged STALLED, and the agent is nudged to split after 5.

## Projects that start from GOAL.md

A project can be a folder with only a `GOAL.md`. The driver sets it up and writes task 000: turn the goal into
`PLAN.md` and a queue of tasks with testable acceptance criteria. When the queue is empty, task 999 checks the project
against `GOAL.md` point by point and adds tasks for whatever is missing; the loop stops after a check that adds
nothing (or after 3 rounds for the same goal). 000 and 999 count as the human's tasks.

- **The goal changed:** when `GOAL.md` differs from the version last planned against, the driver reopens 000 with
  the difference before the next session.
- **Agents never edit `GOAL.md`.** An edit would reopen planning, which took hours on frontpage.
- **Numbers in `GOAL.md` are intuition, not measured requirements** (the human's call, 2026-10-06). A number stands for
  its intent (a 0.5 s page: it feels instant). When the exact number would cost far more than it is worth, the agent
  picks the target its evidence supports, records it under `## Deviations from GOAL.md` in `PLAN.md` with that evidence,
  and the goal check accepts it. A difference the human would notice goes to them as a request. There is no fixed
  tolerance: any percentage would be as arbitrary as the number itself.

## The agent and the human

### Messages to the agent

What the human types in `agent-watch` (or sends from the telecloak app) reaches the running session after its current
turn. Esc stops the session instead, and the next one starts with the message.

### Requests to the human

The agent files a request with the `ask_human` tool. It is saved as `.agent/asks/<n>.md` and reaches the human's phone
over Telegram as plain text; the human answers by replying to it there, or with `agent-talk` on the VM. A project
marked confidential (`.agent/confidential`, in a team's main checkout) gets encrypted messages instead, answered in the
telecloak app. Encryption is off by default (the human's choice, 2026-10-06: it is for confidential projects only).

- **The request makes its task wait, not the loop.** The loop works on other tasks meanwhile and comes back to this one
  first once it is answered. A team agent with nothing else to build prepares upcoming tasks.
- **Every request names the agent's recommendation.** The tool refuses a request without one, unless it is
  `human_only`: something only a person can do (hardware, a credential, money, an account, anything outside the VM).
- **The deadline.** Once the loop has had nothing to build for 2 hours with requests open (`ASK_AUTO_MIN`, 120), it
  answers them itself: go ahead with your recommendation, and record it as `AUTO-DECIDED`. The human gets a notice and
  can still override; a later answer reaches the agent as a message. Human-only requests wait however long it takes.
  Why: on 2026-10-06 both frontpage agents sat idle 10-15 hours overnight on two requests while the human was busy.
- **Reminders:** the doorbell rings once per new set of open requests and every 6 hours after.
- **Requests are untrusted input.** The agent reads web pages, so a request could carry an injected command: read any
  command in one before running it. The app shows plain text only.

**How the message travels (telecloak).** The VM never holds the Telegram bot token or the encryption key. It hands the
text to the AI box over SSH, with a key that can only run the relay (no shell, no forwarding, from the VM's address
only). The AI box encrypts it (AES-256-GCM, one key per direction) and sends it, so Telegram only carries ciphertext.
Replies count only if they come from the human's own account, decrypt, are under a day old and are not replays.
Without a key, only a fixed "help, I need your attention" ping can leave. Rate limit: 20 messages an hour, 100 a day.

**Plain projects.** A project not marked confidential sends readable text. A plain message from the human counts if it
replies to one of the relay's own plain messages (the relay remembers which project and request each one was), or if
exactly one plain request is waiting; it must come from the human's account in the private chat, be under a day old
and be new. It becomes the answer to that request or a message to that project: never a command (start, stop and status
stay encrypted-only), and never anything for a confidential project. The trade-off, accepted: someone holding the
human's Telegram session can talk to plain projects' agents.

## One project per GPU

When a loop starts on a GPU that another project's loop is using, it pauses that loop and waits for it to exit. The
paused loop finishes its current session first, so no work is lost and the card is never idle in between. The newest
start wins, and agents of the same team share their GPUs as configured. The model server's address stands for the GPU.

Each loop registers a lease file, `~/.agent-kit/gpu/<host_port>/<pid>`. It is a file rather than a held lock because an
agent's child processes would inherit an open lock and keep the GPU "taken" after the loop died. A lease whose process
is gone is ignored and removed. Why: the human wants every GPU pointed at one project to work on that project only.

## Team mode

Several agents build one project at once, one per GPU: agent a on the 3090 Ti and agent c on the 3090 (vLLM, 150k
window each), agent b on the 5060 Ti (llama.cpp, 114k window), the same model on all three. `main` holds only
finished, verified work; each agent works in its own worktree (`<project>.<id>`) on its own branch (`agent/<id>`),
with its settings in `.agent/team.env`. `agent-team add` brings an agent into a running team: its task registry is
merged from the others', so tasks the agents created do not become the human's.

### The rules (enforced by the driver)

- **Claims.** A task and its subtasks belong to one agent. A claim survives restarts; it can be taken over only when
  its agent's loop has been gone for 2 hours (a heartbeat file shows it is alive).
- **Dependencies and files.** A task is offered only when its `Depends on:` tasks are done in `main`, and never while
  another agent holds a task whose `Touches:` files overlap.
- **Size.** An agent can be limited to tasks that touch at most N files (`TEAM_MAX_TOUCHES`: 8 for agent b). A task too
  big for another running agent gets split first, into new top-level tasks it can take.
- **Who takes what.** A task's rank is the length of the longest chain of open tasks waiting on it. An agent finishes
  its own claims first, the highest rank first. For new work, fast agents take the highest rank first; a slower agent
  (`TEAM_SPEED`) takes the lowest first while a faster one runs, so the fast card rarely waits on the slow one.
- **Takeover.** When the slow agent holds a task that others wait on anyway (it was the only one free) and a faster
  agent has nothing to build, the faster agent asks for it before it prepares anything. The holder's loop gives the
  claim at once if the task is parked, or tells the session working on it to hand over (`.agent/handover-now`) and
  gives it after. Only that task's work moves with it (`team-takeover paths`: its commits by subject and ledger); the
  new owner's first prompt says it was taken over. Requests to the human live in the asking agent's checkout, so an
  answered request about a task another agent now holds is forwarded to that agent's inbox.
- **Handing a task to a bigger agent.** After 3 sessions in a row without a ticked box while a bigger agent waits, the
  small agent gives the task up; its work is kept on a backup branch. Only that task's files leave its branch while it
  holds other claims (a full reset once cost it a parked task's work).
- **Sync and merge.** Before every session the driver merges `main` into the agent's branch. An accepted task goes into
  `main` under a lock, with the tests re-run if `main` changed; a conflict or a new failure reopens the task.
- **Shared memory files merge cleanly.** Journals keep both sides' entries, generated files are regenerated, hand-over
  notes live in task files only the claiming agent edits, and each agent numbers new tasks from its own range.
- **Timing tests run alone.** A test with `perf` in its file name takes a team-wide lock, so another checkout's suite
  cannot slow it down and fail it.
- **Planning for a team.** The planning task must give every task `Depends on:` and `Touches:`. `plan-schedule`
  replays the schedule and sends the plan back once if shared files would make agents wait.
- **Idle and stop.** An agent with nothing to take prepares a task (below) or waits for the others. When nobody holds
  work, one agent runs the goal check; if it adds nothing, the team stops.

### Prep: work for an idle agent

An agent that has nothing to build prepares a task that starts later, nearest to starting first. It writes
`tasks/prep/<id>.md`: what it assumes each dependency will provide (marked seen in `main`, seen on another agent's
branch, or inferred), the plan per acceptance box, the tests, the risks.

- **Only the notes survive.** Afterwards the driver resets the branch to where it started plus one commit with the
  notes, which go into `main` at once.
- **Real work beats prep.** Every 20 s the driver checks whether the task can now be built or other work is free. If
  so, it signals the session to write its notes, and the session ends a few turns later.
- **The notes belong to the task.** Whoever claims the task starts from them, after checking every assumption against
  `main`.
- **Write early.** At 60% of the hand-over limit without notes, the session is told to write what it has.

Why: on the first team project the small card's agent was busy only 44% of the time its loop ran.

### Watching a team

Every claim, wait, merge and conflict goes to `<project>/.agent/team/events.jsonl`. `agent-team status <name>` shows
who holds what. A watcher (`analysis/team-watch.py`) alerts on conflicts, deadlocks, stale claims, agents held up 20+
minutes, and loops that died.

## Tests of the harness

Every driver change comes with a test that fails on the code before it. They run without a model, using a stub agent.

| Test | Covers |
|---|---|
| `test_single_regression.sh` | One agent: identical logs, history and task files with the old and new driver |
| `test_team.sh` | Team mode end to end: planning, parallel work, dependencies, a merge conflict, restart, prep and hand-off |
| `test_team_prep.sh` | Pick order, prep targets, the prep lock, the cut and the hand-off |
| `test_team_takeover.sh` | Own claims by rank, the takeover ask and answer (parked or mid-session), what moves with a task, hand-overs that keep other claims' work |
| `test_team_add.sh` | Adding an agent to an existing team: its checkout, its settings, and who owns each task |
| `test_team_split.sh`, `test_team_handover.sh`, `test_team_deps.sh`, `test_team_restart.sh`, `test_replan.sh` | Splitting for the small agent, handing tasks over, dependency edge cases, restarts, re-planning |
| `test_ask_deadline.sh`, `test_ask_human_ext.mjs` | The request deadline and the recommendation rule |
| `test_gpu_lease.sh` | One project per GPU |
| `test_stall_watchdog.sh`, `test_venv.sh`, `test_wrapup_signal.mjs`, `test_pi_live.py` | Hang handling, pinned Python packages, the prep cut and the takeover hand-over, the live viewer |

All in [analysis/tests/](../analysis/tests/).

## Decisions

| Date | Decision | Why |
|---|---|---|
| 09-26 | Ledger with the harness version on every session | Attribute every outcome to an exact configuration |
| 09-26 | Context compaction always cancelled | A compacted session carried on with a blurred memory of its own work |
| 09-27 | vLLM (HyperQwen) over llama.cpp | ~100 tok/s and 150k context, quality within ~1.3% of 8-bit |
| 09-27 | The agent may install packages and research the web | Its own machine in an isolated network; it was reaching out anyway |
| 09-27 | The agent may split and add tasks; the human's criteria stay fixed | Fixed tasks locked it into a plan it could not adapt |
| 09-27 | `CODEMAP.md` generated from the code | Agents rarely read hand-written detail files but spent effort maintaining them |
| 09-28 | Hand-over at 120k instead of 100k | A hand-over needs ~20k at the 90th percentile; 100k wasted a third of the window |
| 09-28 | Per-task test baselines | A subtask that fixed part of a sibling's tests was rejected for the sibling's failures |
| 09-28 | "Check facts by running code" | The model spent thousands of thinking tokens on arithmetic a one-line script answers |
| 09-28 | "Problems noticed outside the task become new tasks" | The logs show it noticing problems and leaving them |
| 09-28 | Keep only the 6 newest images per request | The server takes at most 8; sessions with more screenshots died with no hand-over |
| 09-30 | Command timeout, stall watchdog | A hung test cost 45 minutes per session, repeatedly |
| 10-01 | "Refactor first, then add" with a hard trigger (function over 50 lines, a one-off flag, copied code) | A 1,400-line file grew by bolting on. A driver-enforced limit was declined by the human |
| 10-03 | No unit tests: end-to-end, then integration, then golden tests | Unit tests share the agent's blind spots, cost more lines than the code, and pinned constants |
| 10-06 | Request deadline; recommendation required | Agents sat idle overnight waiting for the human |
| 10-06 | `GOAL.md` numbers treated as intuition, settled with evidence | The human's numbers are feel, not measurement |
| 10-06 | One project per GPU | Loops of other projects could share a card indefinitely |
| 10-06 | Thinking cap back to 16k | 32k: same score for 1.8x the tokens; 8k: 6 of 14 instead of 11 |
| 10-06 | Takeover: an idle fast agent takes the slow agent's critical task | a waited 47 min while b held 253 (five tasks behind it) and worked on 243b; the 3-session hand-over is for a stuck agent, not a slow one |
| 10-06 | New driver logic in Python helpers, called from the bash | 1,700 lines of bash, and a 10-06 bug was glob order silently becoming policy. No rewrite |

**Decided against (do not re-propose):** a same-model reviewer agent as a done gate (done claims are already honest);
RAG over the code; Codex as the harness (20k+ tokens of built-in prompt); a higher-precision quant or bigger context for
their own sake; locking down the VM's internet access; a driver-enforced function-length limit; test selection by diff.

## What the model is like (Claude's view, 2026-09-28)

A very good executor and a weak engineer-in-charge. Its code is careful and correct: clean modules, edge cases
handled, honest about done (one rejected done claim in about 180 sessions). It falls short on judgment: tests that pin
exact constants, comments that narrate history, no instinct to refactor, big-bang migrations, and it never pushes back
on a spec. A job it needed 35-40 hours for would take Claude about one working day interactively; the local model does
it unattended for a few dollars of electricity.

## Measurements behind the design

From hollowdeep, a browser game the agent built over ~40 hours (7,368 lines of code, 12,221 of tests, ~300 commits):

- 98% of session time is the model generating; tests are 2%. 72% of generated tokens are thinking.
- About half of each session passes before the first code edit, mostly in one or two long planning turns.
- Task size was the biggest problem: three large tasks took 26-32 sessions each. Once the agent could split tasks, the
  rest of one took 9 sessions instead of the 24 already spent.
- The journal grew past what one read returns, so agents missed the newest entries: hence the archive.
- Tool errors rise with context depth: 0.4% in the first 40k tokens, 3-4% up to 100k, 6.5% beyond.

## Do-later

- **A streamed plan:** the planner publishes each finished first-wave task at once, so the other agents start building
  while it plans the rest (planning idled the other card 14% of the time on frontpage).
- **A bigger model for planning and goal checks:** both 24 GB cards serving one larger model for the phases where
  judgment matters most.
- **The quantization test as a capability test:** with 48 GB of VRAM the 8-bit model fits; compare 4-, 6- and 8-bit
  on real tasks with hidden tests instead of perplexity.
- A kickoff flow: from a one-line idea, the agent drafts the goal and plan; the human approves the criteria.
- Best-of-2 attempts on stuck tasks in separate worktrees, the tests pick the winner.
- A simulation-view tool for games, so the agent can see emergent bugs.

## Commands (on the harness VM)

| To | Run |
|---|---|
| Start or resume a project | `agent-start <project>` (runs in the background, survives logout; pauses another project's loop on the same GPU) |
| Start a team | `agent-team init <name>`, then `agent-team start <name>` |
| Add an agent to a team (a new GPU) | `agent-team add <name> <id>` (settings in `~/.agent-kit/agents/<id>.env`), then `agent-team start <name>` |
| Watch live and talk to the agent | `agent-watch <project>` (type a message and press Enter; Esc stops the session with the message; Ctrl-C leaves the view) |
| Stop | `agent-stop <project>` (after the current session; `--now` immediately), or `agent-team stop <name>` |
| Restart a team after a harness change | `agent-team restart <name>` (each agent finishes its session first) |
| See how it is doing | `agent-report <project>`, `agent-team status <name>`, `sprint-progress <project>` |
| Answer requests | Reply to the Telegram message (the telecloak app for confidential projects), or `agent-talk` on the VM |
| Veto a task the agent added | Set its first line to `Status: dropped` |
| Where things are | Ledger `.agent/iterations.jsonl`, loop log `.agent/loop.log`, sessions `.agent/sessions/`, template `~/.agent-kit/template` |

## Lessons

1. **The agent's logs are the spec for the harness.** Every rule that failed and every mechanism that worked was
   visible in the session files before anyone argued about it.
2. **Ownership beats instructions.** Letting the agent split tasks fixed more than any rule about task size.
3. **Verify remote state before reporting it.** An interrupted command had already done its work; the report said
   otherwise.
4. **Thinking caps cut both ways.** Too low and the model writes code from an unfinished plan; too high and it thinks
   at length for nothing.
5. **The limit on parallel agents is context memory, not compute.**
