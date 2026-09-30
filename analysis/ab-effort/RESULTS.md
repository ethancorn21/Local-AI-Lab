# Thinking-effort A/B, round 1 (2026-09-29/30): xhigh vs medium

Question: does Qwen3.8-27B's `medium` reasoning effort (the chat template offers low, medium, xhigh; "high" maps to
xhigh) finish work with fewer tokens, and at what cost in quality? Replay of type1-triage task 004 (streaming
windower, `56a50bd`), 3 runs per arm, interleaved X1 M1 X2 M2 X3 M3, one agent on the GPU (vLLM showed at most one
running request throughout). Setup, runner, graders and analysis: this folder.

## Cost

| Arm | Effort | Wall min | Sessions | Turns | Output tokens | Thinking share | Done verified by the driver |
|---|---|---|---|---|---|---|---|
| X1 | xhigh | 8.1 | 1 | 22 | 47,157 | 0.70 | yes |
| X2 | xhigh | 90.1 (stopped) | 3 | 24 | 83,382 | 0.81 | no: its test hung, 2 sessions ran into the 45 min limit |
| X3 | xhigh | 14.7 | 2 | 32 | 84,696 | 0.77 | yes |
| M1 | medium | 8.2 | 1 | 23 | 43,772 | 0.65 | yes |
| M2 | medium | 7.0 | 2 | 26 | 38,025 | 0.71 | yes |
| M3 | medium | 4.9 | 1 | 18 | 27,338 | 0.73 | yes |

Medium used about 40% fewer output tokens (median 38k vs 66k for X1/X3) and less time.

## Quality: hidden grader v2 (`grade2.py`, 12 categories, never shown to the agents)

| Impl | Score | random | span | count | lateness bound | adversarial | ties | ticks | lateness param | ts 0 | flush | isolation | scale |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| original (live) | 1.000 | 300/300 | 24/24 | 32/32 | 6/6 | 200/200 | 100/100 | 150/150 | 160/160 | 60/60 | 12/12 | 2/2 | 2/2 |
| X1 | 1.000 | 300/300 | 24/24 | 32/32 | 6/6 | 200/200 | 100/100 | 150/150 | 160/160 | 60/60 | 12/12 | 2/2 | 2/2 |
| X2 | 0.744 | 298/300 | 24/24 | 4/32 | 6/6 | 199/200 | 96/100 | 150/150 | 160/160 | 51/60 | 6/12 | 1/2 | 0/2 |
| X3 | 1.000 | 300/300 | 24/24 | 32/32 | 6/6 | 200/200 | 100/100 | 150/150 | 160/160 | 60/60 | 12/12 | 2/2 | 2/2 |
| M1 | 1.000 | 300/300 | 24/24 | 32/32 | 6/6 | 200/200 | 100/100 | 150/150 | 160/160 | 60/60 | 12/12 | 2/2 | 2/2 |
| M2 | 0.958 | 300/300 | 24/24 | 32/32 | 6/6 | 200/200 | 100/100 | 150/150 | 160/160 | 60/60 | 12/12 | 2/2 | 1/2 |
| M3 | 0.970 | 282/300 | 24/24 | 32/32 | 6/6 | 192/200 | 93/100 | 148/150 | 135/160 | 59/60 | 12/12 | 2/2 | 2/2 |
| naive (control) | 0.337 | 13/300 | 24/24 | 16/32 | 1/6 | 0/200 | 17/100 | 7/150 | 5/160 | 5/60 | 12/12 | 2/2 | 0/2 |

- **X2** loops forever on some inputs (bursts at the 16-event boundary, some flushes, both scale cases): the same bug
  hung its own test. When it terminates its windows are canon-exact.
- **X3** is correct but slow: 30,000 events inside one second took 52.7 s (others 0.2-0.4 s), close to the 60 s
  limit.
- **M2** is correct but too slow: over 60 s on the 30,000-event burst, 5.5 s on 200,000 events (others ~1 s).
- **M3** has real windowing bugs (wrong windows in 7 categories, worst on other `max_lateness` values: 135/160).
- **Flush rule:** task 004 contradicts itself (flush 60 s after the newest event vs exact equality with
  `canon.windows` under 10 s lateness). X2 and X3 chose exactness (wait out the lateness), the others chose the
  60 s rule. Grader v2 accepts both; grader v1 (`grade.py`) ticked the end of each stream at +61 s and wrongly failed
  X2 and X3 on equivalence (found by a second review).

## Reading

Perfect and fast: 1 of 3 per arm (X1, M1). The other runs failed differently: xhigh with a hang and a slow
solution, medium with a slow solution and a buggy one. At n = 3 this does not show a quality difference, and medium
costs about 40% fewer tokens. A larger A/B (more tasks, spec-reading and code-quality measures, a whole project
through the loop) is `analysis/ab-effort2/`.

## Harness finding

X2's hung test sat until the driver's 45-minute session limit, twice, because Pi's bash tool has no default timeout
(hollowdeep: 11 of 216 sessions ended on that limit). Fixed in three layers, deployed 2026-09-30: the `bash-timeout`
extension (600 s default per command), the driver's stall watchdog (a session without new output for 15 minutes is
stopped with everything it started, and the next session gets a note naming the command that hung), and no rerun of
a test suite that timed out.
