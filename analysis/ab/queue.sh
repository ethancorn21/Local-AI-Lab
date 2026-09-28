#!/usr/bin/env bash
# queue.sh : overnight test queue (agent user, tmux window main:ab-queue). Waits for the hand-over A/B (run.sh) to
# finish, then from the same start commit: arm O = Oh My Pi + the lab's extensions (notes in PROGRESS.md, compare with
# arm A), arms A2 and B2 = repeats of A and B (run-to-run noise). Then resumes the live hollowdeep loop.
set -u
until [ -f ~/ab/DONE ]; do sleep 60; done
echo "== queue start $(date '+%F %T')"
bash ~/ab/setup-arms.sh O:A A2:A B2:B
export MAX_ITERS=12
echo "== arm O (Oh My Pi, PROGRESS.md) $(date '+%F %T')";  AGENT_CMD=omp HANDOVER_MODE=progress ~/ab/bin/agent-loop ~/ab/O/hollowdeep
echo "== arm A2 (Pi, PROGRESS.md) $(date '+%F %T')";       AGENT_TMUX=main:ab-agent VIEWER=tui HANDOVER_MODE=progress ~/ab/bin/agent-loop ~/ab/A2/hollowdeep
echo "== arm B2 (Pi, task-file notes) $(date '+%F %T')";   AGENT_TMUX=main:ab-agent VIEWER=tui HANDOVER_MODE=task ~/ab/bin/agent-loop ~/ab/B2/hollowdeep
echo "== queue finished $(date '+%F %T')"; touch ~/ab/QUEUE_DONE
# resume the live loop in its own window (the window's shell sits in the project directory)
rm -f ~/projects/hollowdeep/.agent/PAUSE
tmux send-keys -t main:loop C-u "agent-loop ." Enter
echo "== live loop resumed $(date '+%F %T')"
