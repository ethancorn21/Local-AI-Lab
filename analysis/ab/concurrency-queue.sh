#!/usr/bin/env bash
# concurrency-queue.sh : two agents on ONE card, real conditions (agent user, tmux window main:ab-queue).
# (run 2: the live loop is already paused and switched to task-file notes)
# 3. run two replays of task 025 (arms C1, C2: Pi, task-file notes, production limits) AT THE SAME TIME while sampling
#    the model server's metrics every 30 s; 4. resume the live loop. Compare with the single-agent runs B/B2.
set -u
L=$HOME/projects/hollowdeep
# --- the test
bash ~/ab/setup-arms.sh C1:B C2:B
M=~/ab/concurrency-metrics.csv; echo "time,running,waiting,preemptions_total,gen_tokens_total,kv_usage" > $M
( while [ ! -f ~/ab/CONC_DONE ]; do
    curl -s -m 5 127.0.0.1:8080/metrics | awk -v t="$(date +%s)" '/^vllm:num_requests_running/{r+=$2} /^vllm:num_requests_waiting/{w+=$2} /^vllm:num_preemptions_total/{p+=$2} /^vllm:generation_tokens_total/{g+=$2} /^vllm:(gpu_cache_usage_perc|kv_cache_usage_perc)/{k=$2} END{printf "%s,%d,%d,%d,%d,%.3f\n", t, r, w, p, g, k}' >> $M
    sleep 30; done ) &
export MAX_ITERS=12 HANDOVER_MODE=task VIEWER=tui
echo "== arms C1 + C2 concurrently $(date '+%F %T')"
AGENT_TMUX=main:ab-agent ~/ab/bin/agent-loop ~/ab/C1/hollowdeep > ~/ab/C1.out 2>&1 & c1=$!
sleep 20
AGENT_TMUX=main:ab-agent2 ~/ab/bin/agent-loop ~/ab/C2/hollowdeep > ~/ab/C2.out 2>&1 & c2=$!
wait $c1 $c2   # not a bare wait: the metrics sampler runs until CONC_DONE
touch ~/ab/CONC_DONE; echo "== concurrency test finished $(date '+%F %T')"
# --- resume the live loop (task-file notes are now the driver default)
rm -f "$L/.agent/PAUSE"; tmux send-keys -t main:loop C-u "agent-loop ." Enter
echo "== live loop resumed $(date '+%F %T')"
