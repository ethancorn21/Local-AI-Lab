#!/usr/bin/env bash
# Runs the hand-over A/B, arm A then arm B, in the tmux window it is started from (agent user).
# Agent sessions show in tmux window main:ab-agent (Pi's TUI, as in production). Each arm stops when 025 is done
# (no later tasks in the copies) or after 12 sessions.
export MAX_ITERS=12 AGENT_TMUX=main:ab-agent VIEWER=tui
echo "== arm A (PROGRESS.md) $(date '+%F %T')"; HANDOVER_MODE=progress ~/ab/bin/agent-loop ~/ab/A/hollowdeep
echo "== arm B (task-file hand-over) $(date '+%F %T')"; HANDOVER_MODE=task ~/ab/bin/agent-loop ~/ab/B/hollowdeep
echo "== A/B finished $(date '+%F %T')"; touch ~/ab/DONE
