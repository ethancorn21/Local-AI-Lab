# Local compute

Status (2026-10-24): running on the AI box in place of the production model servers and the agent harness. Claude
calls it through MCP, GrokBot and scripts through HTTP. The setups and their settings come from the
[serving bench](serving-bench.md).

## What it is

On 2026-10-17 the agent harness was scrapped for one service that callers hand work to with "setup X, do this". The
service queues the work, keeps one setup loaded on the two RTX 3090s, switches when the queue asks for another, and
runs each job there. A caller never starts or stops a model server; it names a setup or a kind of job.

```
 Claude Code ──MCP (lc-mcp)──┐
 GrokBot ──HTTP, tailnet─────┤        AI box
 the owner ──lc CLI──────────┼──► lcd (127.0.0.1:8080, :8081) ──► one setup on both 3090s
 scripts ──HTTP──────────────┘     queue · tokens · approvals       main | swarm | big (lc-setup)
                                   jobs: chat, swarm, implement, verify
```

## Setups

| Setup | Model and engine | Context | What it is for | Measured (serving bench) |
|---|---|---|---|---|
| main | Qwen3.8-27B, production's 4-bit build; vLLM, tensor parallel over both 3090s, MTP with 1 draft token, a Mamba checkpoint every 4,080 tokens | 262k (8 agents at 128k) | the default: chat, the implementation agent, the swarm's check | 8 agents at 128k, 35 tok/s each; 81-90 tok/s alone; 30/42 held-out coding attempts, 12 of 14 tasks with retries |
| swarm | Qwen3.6-35B-A3B, 4-bit AWQ; vLLM over both 3090s | 64k a scout | swarm jobs | about 42 of 60 planted bugs in a 44k-line codebase in 30 minutes |
| big | Qwen3.8-Flash-Next IQ3_S on Strata, split by hand at layer 27 | 262k | one long read: a whole codebase or log in one prompt | 118-121 tok/s for one request; reads 175k tokens in 46 s (main: 188 s); holds up best at 191k |

Only one setup runs at a time: each takes both 3090s. A switch costs 1-3.5 minutes. The RTX 5060 Ti stays with the
ancient-works matchers that own it; the bench measured a 4B swarm on it beside main at no cost to main, but at the
4B's quality, so it is not a setup.

## Callers

**Claude** (Claude Code, every project): the MCP server `server/localcompute/lc-mcp`, registered once with
`claude mcp add --scope user localcompute -- python3 <repo>/server/localcompute/lc-mcp`. Its tools:

| Tool | Does |
|---|---|
| `ask` | one question to main or big, with local files inlined; waits for the answer (up to 5 minutes, then a job id) |
| `swarm` | packs a folder (git-tracked files), sends it, starts a swarm job: a bug hunt, or a brief of what to look for; `verify` adds main's check |
| `implement` | packs a folder, sends a spec and a test command; the implementation agent edits a sandboxed copy until the tests pass |
| `job` | a job's state and result: an answer, findings (saved in full to `~/.cache/localcompute/`), or a patch file with the command that applies it |
| `status`, `cancel` | what is loaded and waiting; cancel a job |

The tool descriptions carry the measured numbers, so Claude can judge when a hand-off pays. The local models are
weaker than Claude: a patch is a proposal Claude reviews, swarm findings are leads.

**GrokBot** (on its VPS, tailnet tag `grokbot`): the tailnet policy already lets it reach the box's ports 8080 and
8081 and nothing else, and `tailscale serve` forwards both to lcd. It talks to the OpenAI-compatible door:

- base URL `http://<the box's tailnet name>:8080/v1`, header `Authorization: Bearer <GrokBot's token>`
- `model`: `main` (default; any unknown name means main), `swarm` or `big`
- `POST /v1/chat/completions` (streaming works), `GET /v1/models`

Its swarm and implement jobs (the `/jobs` API below) wait for the owner's approval, the owner's rule of
2026-10-17: the owner's phone gets the request with its id, and `lc approve <id>` or `lc cancel <id>` answers it.
Its chats need no approval.

**The owner and scripts**: the `lc` CLI (`server/localcompute/lc`, linked into the PATH on the Mac and the box):
`lc status`, `lc jobs`, `lc job <id>`, `lc ask "..." [--setup big] [--file F]`, `lc cancel <id>`, and with the admin
token `lc approve <id>`, `lc pause [--stop]`, `lc resume`, `lc load <setup>`. The address and tokens live in
`~/.config/localcompute/` (`url`, `token`, `admin-token`), or `LC_URL`, `LC_TOKEN`, `LC_ADMIN_TOKEN`.

## HTTP API

Every request but `/health` carries a bearer token.

| Request | Does |
|---|---|
| `POST /v1/chat/completions`, `GET /v1/models` | OpenAI-compatible; waits (up to 30 min) for its setup to be loaded |
| `POST /uploads` | body: a `.tar.gz` (at most 64 MB, 512 MB unpacked) of the files a job works on → `{"upload": id}`; only its uploader can use it |
| `POST /jobs` | `{"kind": "chat", "setup", "messages", "thinking", "max_tokens"}`, `{"kind": "swarm", "upload", "brief", "glob", "areas", "verify"}` or `{"kind": "implement", "upload", "spec", "test_command", "protect", "attempts", "max_turns", "test_timeout"}` |
| `GET /jobs`, `GET /jobs/<id>[?wait=N]` | the caller's own jobs; `wait` holds the request up to N s (at most 600) for the job's end |
| `POST /jobs/<id>/cancel`, `POST /jobs/<id>/approve` | approve: admin only |
| `GET /status` | loaded setup, switches, queue (other callers' jobs without ids or content) |
| `POST /admin/pause` (`{"stop": true}` frees the GPUs), `/admin/resume`, `/admin/load` | admin only |

## Jobs

- **chat**: one request to a setup, thinking on by default (8,192 thinking tokens, then the answer).
- **swarm**: the bench's best setting. The folder's text files matching `glob` are cut into areas of about 440
  lines (at most 100), and the scouts (`scout.py`, rules 2: report as you go, a read that would overfill the 64k
  context is refused) work through them with only as many at once as 85% of the server's KV pool holds at 40k each
  (`pool_fit`). With all 100 at once every model spent its time reading conversations again: 4.5 times slower and
  fewer finds. No thinking (it found no more for three times the noise), whole context (rolling context lost on every
  model). Reports within 3 lines of each other are one finding; the result lists the most-reported first.
- **verify** (a swarm job's `verify`): after the swarm, a child job on main checks each finding against the 50 lines
  around it, thinking on, and the swarm job ends with a verdict on each. It costs a switch and some minutes; the
  bench's measure of what it keeps is in [serving-bench.md](serving-bench.md).
- **implement**: the implementation agent, the local model as a tight implementation agent working from a spec,
  checked by tests it may not edit. Tools: list, read, search, write a file, edit a file (exact text, once),
  `run_tests`, finish. `finish` is refused until the tests have passed since the last change. Test files are
  protected by default (`tests/*`, `test_*.py`, `*_test.py`, `conftest.py`, `*.test.*`, `*.spec.*`; `protect`
  replaces the list). Thinking on, 4,096 tokens a turn, 60 turns, 128k context. `attempts` (1-4) run in parallel on
  separate copies; the first to pass wins and the others stop. The result is a unified diff of the files the agent
  changed (`git apply` or `patch -p1`), the end of the last test run and the agent's summary; when nothing passed,
  every attempt's diff. The bench's best use of the 27B was retrying against tests until an attempt passes (12 of 14
  held-out tasks); `run_tests` in the loop is that retry.

**The tests' sandbox.** `run_tests` runs the test command under bubblewrap: the project at `/work`, read-only `/usr`
and the few `/etc` files Python needs, its own `/tmp`, no network, no other process, no `/home`, `/opt`, `/var` or the
service's state, in a systemd scope of at most 512 tasks and 8 GB, with a time limit (default 600 s). The box's
system Python 3.14 and pytest are there; project packages are not.

## Scheduling

One setup is loaded at a time. Each job kind has its setup (implement and verify: main; swarm: swarm; chat: the one
named). A setup runs its own queue in order, main up to 8 seats (an implement job takes one per attempt), swarm and
big one job at a time. When work for another setup waits:

- if the loaded setup has nothing queued, the service switches as soon as what runs has finished;
- if the loaded setup keeps getting work, after 10 minutes (`LC_MAX_WAIT`) it stops taking new work, finishes what
  runs, and the service switches. So no caller waits behind another for long, and a switch is not paid for every job.

Idle on swarm or big for 15 minutes (`LC_IDLE_RETURN`), the service goes back to main, so a chat finds main loaded.

**Held.** If the loaded server stops without the service stopping it (the thermal guard stops the 3090s' servers at
88 C, or a crash), the service holds: chats get 503, jobs wait, the owner's phone is told, and nothing loads until
`lc resume`. Restarting after a heat stop is a human decision, as it always was.

**The bench comes first.** While a `bench-*` container (the serving bench's) or the old production servers run, the
service loads nothing and waits. To run a bench: `lc pause --stop`, bench, then `lc resume`.

## Security

- Tokens are random (`lc_` + 43 characters), stored only as SHA-256 hashes in `/etc/localcompute/tokens.json`
  (mode 600). `lcd.py token add <name> [--admin] [--approve swarm,implement] [--scopes ...]` prints a new one once.
  Three exist: Claude's, GrokBot's (its swarm and implement jobs held for approval) and the owner's (admin).
- A caller sees only its own jobs and uploads; `/status` shows other callers' work without ids or content.
- lcd listens on 127.0.0.1 only. The tailnet reaches it through `tailscale serve` on 8080/8081: members, and
  `tag:grokbot` by the tailnet policy, which allows it nothing else on the box.
- Content is kept only while a job needs it: uploads for a day, job results for 7 days; workspaces are deleted when
  a job ends. The chat request log (`/var/lib/localcompute/requests.log`) holds who, which setup, the token counts
  and the time, never content.
- Model-written code runs only inside the tests' sandbox.

## Operations

| What | Where |
|---|---|
| Service | `localcompute.service` (runs `/opt/llm/localcompute/lcd.py serve`, as the admin account with the docker group); `journalctl -u localcompute` |
| Code | `server/localcompute/` → `/opt/llm/localcompute/` (with `server/bench/scout.py`) |
| Setups | `lc-setup start <setup> <port>`, `stop`, `running`, `gpu-busy`; containers `lc-main`, `lc-swarm`, `lc-big` on 127.0.0.1:8101-8103; engine logs saved to `/var/lib/localcompute/setup/` when a setup stops |
| State | `/var/lib/localcompute/` (jobs.db, jobs/, uploads/, requests.log) |
| Thermal guard | `server/thermal/hwtemps-log`: a 3090 at 88 C stops any `lc-*` or `bench-*` container (since 2026-10-24; the bench's ran unguarded before) |
| Production containers | `hyperqwen-single-1` and `hyperqwen-c-single-1` stay stopped, restart policy `no` (they held one card each; the service needs both) |

Deploy: copy `server/localcompute/*` and `server/bench/scout.py` to `/opt/llm/localcompute/`, then
`sudo systemctl restart localcompute`. A running model server survives the restart (`KillMode=process`) and the new
lcd adopts it.

## Tests

`analysis/tests/test_localcompute.py` runs lcd end to end with a stub `lc-setup` that starts stub OpenAI servers
answering with scripted tool calls: no model, no GPU, about a minute (needs bubblewrap, so it runs on the box). 41
checks: tokens, the hold while the bench runs, the idle setup, the OpenAI door plain and streamed, a switch to big
and back, chat jobs, a swarm with main's check (main → swarm → main), globs, the implementation agent (a protected
test file refused, finish refused before the tests pass, the patch applying with `patch -p1`), the sandbox (no
`/home`, no `/opt`, no network), GrokBot's approval and the owner's ping, job and upload privacy, cancel, a setup
that stops on its own (held, 503, resume), and the MCP server over stdio. Run it with `LC_SANDBOX_SCOPE=1` to use the
systemd scope as the service does.

## Not done, on purpose

- No web search for the scouts or the agent: they read what they are given. (keenable.ai or the VM's SearXNG, if a
  job needs it.)
- The implementation agent runs tests with the system Python only: no project dependencies, no other languages yet.
- The 5060 Ti is not a setup (above).
- GrokBot's side (its software and how it calls models) is configured by the owner: the base URL and its token are
  all it needs.
