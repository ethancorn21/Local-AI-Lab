# Local AI Lab

A home lab that runs an open-weight coding model on consumer GPUs and lets autonomous agents build software with it,
unattended, for days. This repository is the technical side: the harness code, configs, benchmark data and
experiments.

**Current setup:** Qwen3.8-27B (4-bit), one copy per GPU, one agent per copy: an RTX 3090 Ti on vLLM (~100 tok/s,
150k context) and an RTX 5060 Ti on llama.cpp (~28 tok/s, 114k). A second 24 GB card, an RTX 3090, is being added. The
agents (the Pi coding agent with this repo's extensions and driver) work in an isolated VM and build the same project
together, each on its own git branch.

**Start here: [How the harness works](docs/how-it-works.md).**

## The idea in one paragraph

A human writes what should exist (`GOAL.md`) and owns what "done" means. Local agents work out how: they plan, split
the work into tasks, write the code and tests, and run for days. Each agent session is disposable: a fresh agent does
one step, writes down what it learned, commits and exits, so memory lives in files and git. A plain bash script, not a
model, decides what runs next, checks every "done" claim and merges the work, because a script cannot be talked into
"basically passing". A cloud model (Claude) designs the harness from the agents' own session logs but does not steer
them while they work.

## Highlights

- An agent built a browser game from three successive designs: ~7,400 lines of code, ~12,200 lines of tests and ~300
  commits in ~40 hours of unattended work, with one rejected "done" claim in ~180 sessions.
- Two agents on two cards built a personal reading feed (frontpage) from a one-page goal: 64 tasks, ~8,100 lines of
  code and ~12,000 lines of end-to-end, integration and golden tests, no task lost or done twice.
- Every rule that was enforced in code held; every rule written only as instructions to the model failed. The harness
  is built on that ([agent-harness.md](docs/agent-harness.md)).
- Letting the agent split its own tasks finished one in 9 sessions that had taken 24 sessions to get half done.
- Measured, not assumed: two agents on one card give only ~1.06x one agent; a 16k thinking cap scores as well as 32k
  for half the tokens, and far better than 8k ([experiments.md](docs/experiments.md)).

## Layout

| Path | What |
|---|---|
| [docs/how-it-works.md](docs/how-it-works.md) | The guide: start here |
| [docs/agent-harness.md](docs/agent-harness.md) | Every rule with its reason, the harness's tests, decisions, commands |
| [docs/experiments.md](docs/experiments.md) | What was measured and what it decided; long write-ups: [effort-ab.md](docs/effort-ab.md), [model-choice.md](docs/model-choice.md), [type1.md](docs/type1.md) |
| [docs/ai-lab.md](docs/ai-lab.md) | Hardware, power and heat, model serving, network and security, changelog |
| [docs/history/](docs/history/) | The first build, team mode's first run, the detailed changelog |
| [server/](server/) | The GPU box: power limits, the thermal guard, vLLM and llama.cpp configs, the encrypted doorbell relay, benchmarks |
| [harness/](harness/) | The agents' VM: the driver, Pi extensions, the project template, operator tools, config examples |
| [analysis/](analysis/) | Session-log analysers behind every number in the docs, experiment kits, the driver's tests |

## Using it

The pieces assume the setup in [docs/ai-lab.md](docs/ai-lab.md): model servers reachable on the agents' machine at
`127.0.0.1:<port>` (one SSH tunnel per port), and the Pi coding agent installed for an unprivileged user.

1. Copy `harness/driver/*` and `harness/tools/*` to `~/bin`, `harness/pi-extensions/*.ts` to
   `~/.pi/agent/extensions/`, `harness/template/` to `~/.agent-kit/template/`, and start from `harness/pi-config/` for
   Pi's `models.json` and `settings.json`.
2. **One agent:** make a folder in `~/projects/` with a `GOAL.md` (what you want, in your own words) and run
   `agent-start <name>`. Edit `GOAL.md` any time; the next session re-plans.
3. **A team:** one settings file per agent in `~/.agent-kit/agents/` (from `harness/agents/*.env.example`), then
   `agent-team init <name>` and `agent-team start <name>`.
4. Watch and steer with `agent-watch <name>`; stop with `agent-stop <name>` or `agent-team stop <name>`.

Configs ending in `.example` have placeholders (`<...>`, `CHANGE-ME`) in place of addresses, account names and secrets.

## Publishing hygiene

Addresses, VLAN numbers, account names, keys and raw agent session logs are kept out of this repository. A gitleaks
pre-commit hook checks every commit for secrets: run `scripts/install-hooks.sh` once after cloning.

## Credits

- [Pi coding agent](https://github.com/earendil-works/pi) (the harness these extensions plug into)
- [HyperQwen](https://github.com/syv-ai/HyperQwen) (the vLLM build and model recipe) and [vLLM](https://github.com/vllm-project/vllm)
- [llama.cpp](https://github.com/ggml-org/llama.cpp), [SearXNG](https://github.com/searxng/searxng), [trafilatura](https://github.com/adbar/trafilatura)
- Qwen3.8-27B by the Qwen team

Built with Claude (Anthropic) as architect and pair; the agents' code is written by the local model.
