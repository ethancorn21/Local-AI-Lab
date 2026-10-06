# Team mode's first real run: frontpage, 2026-10-02 to 10-04

Moved here from agent-harness.md on 2026-10-06, unchanged. The rules these fixes produced are described in
[agent-harness.md](../agent-harness.md#team-mode); this page keeps the story of how each one was found.

The first team project was also team mode's first real test. Every problem below was found by the watcher or by the
human, fixed in the driver with a test, and deployed the same day.

| What happened | Cause | Fix |
|---|---|---|
| Agent b idle for most of a re-plan | Planning is one agent's job, and two GOAL.md changes meant two planning passes | By design; plans are now cheaper to keep parallel (next rows) |
| After the plan, b waited again: 7 of 9 tasks hung off one task | Hotspot files: `config.py`, `README.md` and the sample config were in almost every task, so the tasks lined up | Plans with a file in more than 3 open tasks are rejected (replaced 2026-10-04, next row) |
| An agent re-took a task the other had just finished | Its checkout synced a moment before the other agent's merge; the claim was free | Claims are checked against main |
| After a restart the agents swapped tasks (b took a's half-done task, a took b's) | A claim was tied to a loop's process id; a restarted loop looked like a crashed one | Heartbeat-based claims (above) |
| b spent three sessions on a 14-file task without writing anything | Orientation plus reading 14 files filled its 114k window before the hand-over could finish | Per-agent task size limit; hand-over for b at 75k (llama.cpp ignores the hand-over turn's 2k thinking cap) |
| A merge conflict on a test file | b added the file to its `Touches:` mid-task, after a had claimed a task on it; the overlap check runs only at claim time | Caught by the merge step as designed. Idea, not built: warn both agents when a task's files grow into another agent's task |
| The second goal-check round spun every 2 s (1,500 events) | The claim check above refused the goal check the driver had just reopened | 000/999 exempt; refused claims on that path wait a cycle |
| The same spin again on the other agent an hour later | That agent's loop still ran the old code, and the watcher had not been re-armed | `agent-team restart` after every harness change; the watcher stays armed |
| The goal check ran 19 rounds without stopping | Every round the agent ticked every box and wrote "all met", but left the first line `Status: in-progress` | The driver closes 000 or 999 when every box is ticked, then verifies as usual |
| Sessions hit the context window again and again; DECISIONS.md was 76 KB | Entries of open tasks were never archived, and the goal check adds one per round | An open task keeps its newest 3 entries. The same fix found the archiver losing archived goal-check entries |
| a spent 8 sessions on a task that needed another of its own tasks first | The dependency check only looked at the other agent's tasks | An agent's own tasks wait for their `Depends on:` too; the prompt says to add the dependency and end the session instead of building stand-ins |
| Agent b idle for hours after the re-plan | The plan put a 19-file task first in a chain; b takes at most 8 files, and the plan check only looked for shared files | The agent that takes a too-big task splits it, and the parts go into main at once (above) |
| The goal check ran six sessions while three tasks were still being built | A 999 left open by an earlier round has `Depends on: none`, so the idle fast agent took it | 999 waits until every other task is done in main |
| The planning task ran 16 sessions in a row, each done claim rejected for hotspot files, while b sat idle | The per-file count ignored `Depends on:`: this round's UI tasks share `feed.html`, `styles.css` and the browser tests, but in a dependency line behind an early shared-changes task (the rule's own advice), so the shared files cost nothing: replaying the schedule, 8 rounds either way. Every re-plan still counted 4-6 tasks per file | `plan-schedule` replays the scheduler and advises only when shared files really slow the plan; the advice sends a plan back once per GOAL.md version. The frontpage plan passes |
| DECISIONS.md kept passing 93 KB; a session read up to 63k tokens before working (median 32k at the first real tool call) | PITFALL entries were never archived and could never be rewritten: 77 of them were 87% of the file (64 KB), growing ~30 KB per day of building; one 22 KB planning entry made the 94 KB peak. Every session also read DECISIONS.md (60 of 60) and PLAN.md (44 of 60, 18.9k tokens) whole. Checked against the logs: task 221 had task 212's gunicorn PITFALL in front of it in 16 of 19 sessions and never used it | PITFALLs move to their own curated, searchable PITFALLS.md (contents block read at spawn); the agent gets its own task's journal in PROGRESS.md and searches DECISIONS.md and PLAN.md (000 and 999 read PLAN.md whole). Spawn reads on frontpage: 14.3k tokens instead of 63.5k |
| b spent 10 sessions on 220 (0 of 7 boxes, the last four cut off at the window) while a waited | Nothing moved a stuck task from the small agent to the big one; the third time after 205 and 215 | Hand-over after 3 sessions without a ticked box while a bigger agent waits (above) |
| a sat 25 min on a request from the day before, while the task it needed was being handed to it | The request (non-blocking, about a task finished since) was never closed, and the wait for the human never looked for team work | Team agents wait the team way while others work; accepted tasks withdraw their requests |
| Both agents idle with four tasks left | a split 221 into 221a-d and wrote "Depends on: 221" (the parent) into 221a; the parent waits for its subtasks, and an agent holding only its own claims never reached the deadlock check | A dependency on the own parent is void; only another agent's claim means "wait" |
| STALLED alert at 12 sessions on a re-plan that was 4 sessions old, and the planner was told to split itself | The count covered every session the task ever had, across three earlier plans; the planning file also carried all four GOAL.md diffs (29 KB) | Count since the last accepted done; a finished plan drops its old change notes on reopen (29 KB to 11 KB). `analysis/tests/test_replan.sh` |

| Agent b idle 56% of its running time (Oct 2-4: 6.4 h during planning, 5.9 h during goal checks, 13.0 h waiting on dependencies, claims or tasks too big for it); a busy 92% | One agent plans and one checks the goal (by design); between those, every open task hung off one task a was building (230 in the 10-04 plan), and nothing gave a waiting agent anything else to do | Prep while idle, the cut and the hand-off, critical-path picking (above). Planning and goal checks still idle the other cards: a streamed plan is on the do-later list |
| Lowest number first would hand the slow card a task six others wait on | The pick ignored the graph and the speed difference | Critical-path picking with per-agent speeds |
| The team test planned twice (first run of the pick-order change) | 000 and 999 skip the done-in-main claim check (the driver reopens them); a checkout that synced just before the other agent's merge saw 000 open. The slower rank-ordered pick widened that old window | Claiming 000 or 999 from a checkout behind main syncs first and looks again; a scenario reproduces it |

**Result:** about 6,000 lines of application code and 9,400 lines of tests (a browser end-to-end test included), 40
tasks, 98 agent commits; no task was lost or done twice in the end, and every conflict went back to the agent with the
reason. Agent b (the 5060 Ti at about a quarter of the 3090 Ti's speed) merged 7 of the ~42 accepted tasks: the
smaller card helps on parallel waves and small tasks, and waits on long chains.

**Second day (2026-10-03, review 3 plus the no-unit-tests trial):** two re-plans, 16 tasks, finished 01:14 the next
night with the goal check judging all nine GOAL points met. The driver changes above came out of that day: re-plan
session counts, the split rule, the hand-over rule (it then moved 226 and 100 from b to a by itself), the goal check
waiting for the queue, no waiting on a stale request or on one's own claims. All unit-test files were removed (the
human's decision): the suite went from about 470 tests to 326 end-to-end, integration, golden and speed tests (12,064
lines against 8,138 of application code). One product bug escaped the tests: the match cache was never backfilled
for items from before it existed, so the real database took 2.4 s per page while the speed test (a freshly filled
database) passed. Measured on the live preview, filed as a task with the evidence, fixed: 0.14-0.16 s per page.
