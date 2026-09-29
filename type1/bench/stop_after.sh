#!/usr/bin/env bash
# Let the queue runner finish the job named $1, then start nothing more: once "$1" has started, kill the runner
# (the running job is its child in the background and keeps going).
until grep -q "start $1:" /opt/llm/type1/logs/queue.log; do sleep 20; done
for pid in $(pgrep -f "run_queue[.]sh"); do kill "$pid"; done
echo "$(date -u +%T) runner stopped; $1 runs to the end, nothing after it" >> /opt/llm/type1/logs/queue.log
