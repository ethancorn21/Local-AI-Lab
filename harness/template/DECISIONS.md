# Decisions and dead ends

Your journal: append only, newest at the bottom. When a task is finished, the driver moves its entries verbatim to
DECISIONS-archive.md (search it with grep) and lists the task in an index at the top of this file. Formats:

    ## <date> <task id> DECISION: <the choice>
    Chose X over Y because Z.

    ## <date> <task id> BLOCKER: <short title>
    - Attempt 1: tried A -> failed because B
    - Attempt 2: tried C -> failed because D

    ## <date> <task id> PITFALL: <the trap, stated as a fact>
    Stays true after this task: a tool quirk, an environment limit, a trap in the code. Never archived.

The task id is the task file's number, with its letters for a subtask (e.g. 025a).
