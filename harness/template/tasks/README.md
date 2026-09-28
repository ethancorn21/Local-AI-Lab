# Task format

One file per task: `NNN-short-name.md`, worked in number order. Subtasks of task NNN are `NNNa-...`, `NNNb-...`: they
are worked before their parent, which comes back once they are all finished. The FIRST line must be the status:
`open`, `in-progress`, `split` (worked through its subtasks), `done`, `blocked` or `dropped`.

    Status: open
    # NNN: Title

    ## Goal
    What to build and why, in a few sentences.

    ## Acceptance criteria
    - [ ] Concrete, testable statements (ideally: tests that must pass)

    ## Depends on
    - NNN (optional)

    ## Notes
    Hints, constraints, links to architecture docs.

Ownership: task files present when a loop starts are the human's; their acceptance criteria define done and only the
human changes them (the agent may add a `## Proposed changes` section with what it would change and why). Tasks the
agent creates are its own. One seed task with the goal and acceptance criteria is enough to start a project: the
agent splits it into subtasks.
