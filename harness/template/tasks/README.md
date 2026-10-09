# Task format

One file per task: `NNN-short-name.md`, worked in number order. Subtasks of task NNN are `NNNa-...`, `NNNb-...`: they
are worked before their parent, which comes back once they are all finished. The FIRST line must be the status:
`open`, `in-progress`, `split` (worked through its subtasks), `done`, `blocked` or `dropped`.

    Status: open
    # NNN: Title
    Depends on: NNN, NNN
    Touches: src/feed.py, tests/

    ## Goal
    What to build and why, in a few sentences.

    ## Acceptance criteria
    - [ ] Concrete, testable statements (ideally: tests that must pass)

    ## Notes
    Hints, constraints, links to architecture docs.

The driver reads three lines, exactly as shown (after every session it checks the task files the session changed and
the next prompt says what it could not read):
- line 1, `Status: <word>`: only the first word counts (`Status: done (verified)` is done).
- `Depends on: 229, 231`: the tasks that must be done first, on ONE line; `Depends on: none` when there are none. A
  `## Depends on` heading with a list is not read: such a task has no dependencies.
- `Touches: <files and folders>` (team projects): what the task changes, so two agents never edit the same files.

Ownership: task files present when a loop starts are the human's; their acceptance criteria define done and only the
human changes them (the agent may add a `## Proposed changes` section with what it would change and why). Tasks the
agent creates are its own. One seed task with the goal and acceptance criteria is enough to start a project: the
agent splits it into subtasks.
