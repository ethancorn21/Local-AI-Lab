# Lab v2: one lead, many workers

Status: decided 2026-10-11. Phase 1 built and deployed the same night (Claude leads ancient-works; `lab`, job mode, the console's Lead page). Replaces the team layer described in [agent-harness.md](agent-harness.md)
(claims, file overlap, prep, carve, takeover, hand-over to a bigger agent, idle split, stall net) once the phases
below are done.

## Why

The owner, 2026-10-11, after a night on ancient-works: "Our orchestration layer sucks. We need a single point of
contact for these agents and the rest is routed behind the scenes ... I just want this orchestration madness to stop.
It is too locked down in rigid code."

That night, in one project:

- A request to the owner went to Telegram at 22:37 and sat 2.5 hours. He does not read Telegram, and the architect
  (Claude), who was watching the team, did not mention it either.
- The finished task waiting on that answer held `tests/`, so the other agent could only write prep notes, for 2.5 hours.
- The console showed neither agent: the shipper's project list had never been updated.
- Two of frontpage's three agents had been paused by hand to lend their GPUs, and nothing on the console said why.

Each agent schedules itself through rules in `agent-team-lib` (1,347 lines plus three helpers). Six of its nine
changes from 10-06 to 10-10 answered a new way the agents stalled or sat idle; the fix that night made seven. No one
sees the whole board, so every new situation needs a new rule.

## The shape

```
you ◄──► FRONT DOOR: Claude, in a Claude Code session. One inbox for everything the agents need from you.
            │ intent → goals and tasks          ▲ only what needs you
            ▼                                   │
        LEAD: decides who builds what, on which GPU, for every project
            Claude while a session is open; a local Qwen lead otherwise (never both: a lead lock)
            │ lab run / stop / msg                ▲ job ended: done / blocked / no progress / question
            ▼                                   │
        WORKERS: Qwen sessions on the 3090 Ti, the 3090 and the 5060 Ti; each runs one job
```

- **The owner talks to Claude only.** "Do X" becomes a goal or a task, and Claude hands it off or does it.
- **The lead decides with judgment, not rules.** It reads the board (every project, every task, every GPU, the
  inbox), starts jobs and moves GPUs between projects by the owner's priorities. No more pausing agents by hand.
- **Every lead action goes through a `lab` command, and every command is logged** to the lead journal. The console
  gets a Lead pane: who leads now (Claude or Qwen), what it decided and why, and each worker's live session (already
  there). That is the answer to "I need to see what the agent is doing, and if the sub agents are doing anything".

## What goes, what stays

| Goes: orchestration (the lead decides instead) | Stays: integrity (true whoever leads) |
|---|---|
| Claims and heartbeats, file-overlap waits | A task is done only when its acceptance boxes are met and the tests pass |
| Prep sessions, carve, takeover, hand-over to a bigger agent | One fresh session per step, hand-over notes in the task file |
| Idle split, stall net, deadlock cycles, pick order | Each worker on its own branch; finished work merges into `main` through the test gate |
| Per-agent loops that never end; GPU leases by hand | The owner's acceptance criteria define done; agents may propose changes, never make them |

The integrity rules stay because each one held where a prompt instruction failed (design principle 2). The
scheduling rules go because a lead that sees the whole board can handle what each of them was written for.

## Using it (phase 1)

```
lab board                                         everything at once
lab lead take ancient-works                       lead it (workers finish their session, then wait for jobs)
lab run ancient-works a 213 --why "Imola first"   a job: that task only, until done, blocked or stuck
lab log                                           the journal; also the console's Lead page
lab lead release ancient-works                    back to the team rules
```

A job ends by itself: the task is done and merged (with background verify, `BG_VERIFY=1`: done with its boxes checked
and its tests queued; the journal's `verify_queued`, `verify_passed` and `verify_failed` follow the run, `lab board`
shows it, and a failed task goes back to the same worker before its next job), it waits on a request to the human, 3 sessions in a row make
no commit, or 5 in a row commit only the task file and notes (`JOB_NOTES_ONLY`: a worker re-checking a task it
cannot close). The worker writes why in the journal and waits for the next job. A waiting worker does not sync with main,
so when a job names a task it cannot see, it merges main first: the lead may have just written the task there.

## The pieces

- **`lab`, a small command on the VM**, the only way the lead acts: `lab board` (all of it, one screen), `lab run
  <project> <task> [--gpu]`, `lab stop <worker>`, `lab msg <worker> <text>`, `lab answer <project> <request> <text>`,
  `lab inbox`. It does mechanics only and decides nothing.
- **A worker runs a job**: sessions on one task until it is verified done, blocked on a request, or has gone 3
  sessions without progress. Then it stops and the lead is told why. This is `agent-loop` with a one-task mode.
- **The Qwen lead is one fresh session per event** (a job ended, a request was filed, a GPU came free, the owner sent
  something): it reads the board, the journal's tail and the inbox, acts through `lab`, writes why, and exits. It
  keeps no memory between events, so it cannot lose track over hours. A lead that loses the thread is the reason a
  script sat in the middle until now.
- **The lead lock**: Claude takes it at session start and keeps it fresh while the session is open; when it goes
  stale, the Qwen lead takes over at the next event.
- **The inbox**: open requests, decisions the loop made itself (the auto-answer), finished features, new top-level
  tasks to veto, and the lead's own questions. The console shows it, and a Claude Code session-start hook puts it in
  front of Claude, so reading it is code, not a promise.

## Defaults (the owner can override any)

- **Pilot: ancient-works.** It is the project being watched. frontpage moves over after.
- **The lead writes the tasks.** Workers may still add tasks; each one goes to the lead's board.
- **The Qwen lead uses a worker's model server** (the same model on the same card, a few short requests per hour).
  This is a deliberate exception to "never share a card", which is about a second model loaded on a busy card.
  Measure a worker's tokens/s with and without lead calls; if it costs more than a few percent, the lead gets its own
  slot.
- **A hidden project is led only by the Qwen lead.** Claude never reads inside it, so `lab` opens only the projects
  named in `~/.agent-kit/lead-projects` (an allowlist, like the console shipper's `--only`) and never lists the
  projects folder. Its workers' GPUs move only by the Qwen lead or the owner.
- **Telegram stays off** (2026-10-11).

## Phases

1. **Claude leads ancient-works.** Built 2026-10-11: `lab` (`harness/tools/lab`), job mode in `agent-loop`, the lead
   journal (`~/.agent-kit/lead/journal.jsonl`), and the console's Lead page (who leads, each agent's job, the journal).
   Until phase 3 the team layer still runs frontpage, and runs ancient-works whenever the lead's lock goes stale: no
   `lab` call for 30 minutes, which happens while Claude is busy with something else, not only when the session ends.
2. **The inbox.** `lab inbox` exists (open requests, ended jobs, new tasks, stuck and merged work since the last
   look); the session-start hook that puts it in front of Claude, and the inbox on the console, come next.
3. **The Qwen lead.** Event-driven, with the lead lock. From here nothing waits for a Claude session.
4. **frontpage moves over; the team layer is deleted** (`agent-team-lib`, `team-takeover`, `team-carve`,
   `plan-schedule`, the prep and carve extensions' team parts, and their tests).

Each phase comes with tests that run the real thing with stub agents (house rule 3) and a before/after measurement:
GPU idle minutes per day, and hours a request waits for an answer.
