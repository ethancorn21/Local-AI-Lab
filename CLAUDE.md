# Working on the lab itself

For any agent (Claude or a local model) that changes this repository. The projects the agents build have their own
rulebook (`harness/template/AGENTS.md`); this page is about the harness, the servers and the docs. `AGENTS.md` points
here.

## What lives where

| Path | What |
|---|---|
| `harness/driver/` | The loop: `agent-loop` (bash, one agent), `agent-team-lib` (team mode, sourced by it), `agent-team` (set up / start / status). Small Python deciders it calls: `task-audit`, `plan-schedule`, `team-takeover`, `team-carve`, `timing-watch`, `user-tests`, `decisions-archive`, `pitfalls-sync`, `codemap-gen`. `pylib/` holds pytest plugins |
| `harness/pi-extensions/` | What runs inside an agent session (hand-over, timeouts, requests to the human, messages) |
| `harness/template/` | What a new project starts with: the agents' `AGENTS.md`, the task format (`tasks/README.md`) |
| `harness/tools/` | Operator tools: `agent-start`, `agent-watch`, `sprint-progress`, the doorbell |
| `harness/deploy-driver` | Installs the driver, plugins and extensions on the VM (with backups) and restarts teams |
| `analysis/tests/` | The harness's tests: stub agents, no model (list in `docs/agent-harness.md`, Tests of the harness) |
| `analysis/` | The session-log analysers behind every number in the docs |
| `server/`, `dashboard/` | The GPU box (serving, power, heat, the relay) and the lab console |
| `docs/` | `how-it-works.md` (the guide), `agent-harness.md` (every rule with its reason, decisions, decided against), `experiments.md`, `ai-lab.md` (hardware, network, changelog) |

## House rules

1. **Rules that matter go in code, not in prose to the model.** Every rule that was only an instruction failed
   (`docs/agent-harness.md`, Design principles). Before adding one, read the agents' session logs: design around what
   the agent actually does.
2. **Every rule carries its story**: a comment or doc line with the date, the project and what went wrong ("Why:
   frontpage 2026-10-07, ..."). A rule without one gets removed by the next person who cannot see why it is there.
3. **Test first, without a model.** Every driver change comes with a test in `analysis/tests/` that fails on the old
   driver and passes on the new one. Run them on the VM (they need its tools): copy `harness/driver` and
   `analysis/tests` into a scratch folder there and run e.g. `bash tests/test_team_timing.sh <driver dir>`. After any
   change to shared code also run `test_single_regression.sh <old> <new>` and `test_team.sh`.
4. **Tests drive the real thing**: end-to-end with stub agents, integration, golden. No unit tests (decided 10-03).
5. **The driver stays bash**: new decision logic goes into a small Python helper it calls; bash gathers the facts,
   Python decides, bash acts. No rewrite.
6. **Measure, then decide.** Each session is logged with a fingerprint of the harness that ran it; compare before
   and after. A goal number from the human is intuition, settled with evidence of what a user would notice.
7. **The agents own how, the human owns what.** Never edit a project's `GOAL.md` or the human's acceptance boxes
   except as the human's own change (a `[human]` commit, under the team's merge lock).
8. **Check the decided-against list** (end of the Decisions table in `docs/agent-harness.md`) before proposing
   anything: those were weighed and declined. Do not re-propose them.

## After a change

- Deploy with `EXPECT=<the commit the VM runs> harness/deploy-driver <team>`: it refuses if someone deployed in
  between, backs up what it replaces and restarts each agent after its current session.
- Update the doc that describes the rule (`agent-harness.md`, and `how-it-works.md` if a reader would notice) and add
  a line to the changelog in `docs/ai-lab.md`.
- Commit with no co-author or tool trailer. This repository is public: no addresses, VLAN numbers, account names,
  keys or raw session logs (the gitleaks hook checks secrets: `scripts/install-hooks.sh`). Pushing is the owner's
  call: ask first.
