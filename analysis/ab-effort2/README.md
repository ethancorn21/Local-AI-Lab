# Effort A/B v2: thinking effort measured on the real workflow

Does `THINKING=xhigh` buy anything over `medium` for how the lab actually uses the local model: reading a spec
critically, writing code worth keeping, and carrying a project through many sessions of the loop? v1
(`../ab-effort/`) measured one function with one grader; see `docs/agent-harness.md` for why that was not enough.

## What is measured

- **Spec reading.** Six one-task probes (four specs with one planted flaw each, two clean controls) and two planted
  flaws in the project's GOAL.md. Scored by a blind Claude judge from what the agent wrote (requests, plans,
  decisions, hand-overs, commit messages, comments): noticed or not, and how it was handled. The controls give the
  false-alarm rate.
- **Correctness.** Hidden checks, never visible to the agent. A planted flaw has its own category, and behaviour that
  depends on how a flaw was resolved is taken out of the core categories.
- **Code quality.** Blind Claude rubric per run (and pairwise for the project), mutation score of the agent's own
  tests, ruff issues per 100 lines.
- **Project work.** A multi-step security CLI from a GOAL.md through the full loop: wall time, sessions, tokens and
  thinking share, rejected done claims, stalls, tasks the agent added, requests to the human.

## Files

| File | Where it runs | What it does |
|---|---|---|
| `run.py` | harness VM, as `claude` | `plan` writes the schedule (4 reps x 6 probes x 2 efforts, 3 project reps x 2 efforts, ABBA order); `go [probes\|project]` runs it (resumable; waits for an idle model server; auto-answers requests; samples vLLM load; grades each run); `smoke NAME EFFORT`; `cltest` (auto-responder classifier self-test); `status` |
| `auto.sh` | harness VM | unattended sequencer: waits for the GPU hand-over, classifier test, two smoke runs with a plumbing check, then both phases. `HOLD` file = stop between runs |
| `analyze.py` | harness VM, `sudo` | per-run metrics and per-effort summary; `--packets` writes anonymous judge packets; `--mutate` runs the mutation score (after the GPU runs) |
| `mutate.py` | harness VM, as `agent` on a copy | plants one small bug at a time (comparison, arithmetic, boolean, constant flips) and counts how many the agent's own tests catch |
| `judge.py` | the Mac | `pull` packets, `run [spec\|code\|pair]` through headless `claude -p` (subscription, own system prompt, no tools, no user settings), `report` unblinds |
| `hidden/` | not published until the runs are done | the project GOAL.md, probe seeds, answer keys, hidden checks, reference implementation, judge outputs |

## Running it

```
# VM (as claude), kit in /home/claude/ab-effort2-kit (mode 700: the agent account cannot read it)
python3 run.py plan && python3 run.py cltest
setsid nohup ./auto.sh > /dev/null 2>&1 < /dev/null &      # waits for the GPU_FREE file
python3 run.py status
sudo python3 analyze.py --packets --mutate                 # after ALL_DONE
# Mac
python3 judge.py pull && python3 judge.py run && python3 judge.py report
```
