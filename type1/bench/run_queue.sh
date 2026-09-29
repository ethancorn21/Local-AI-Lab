#!/usr/bin/env bash
# Run bake-off jobs one after another on the GPU (vLLM must be stopped first), with a thermal watchdog:
# the lab's hwtemps guard only stops the vLLM container, so this kills the running job itself if the GPU stays at
# or above 88 C for a minute. Usage: run_queue.sh QUEUE_FILE   (one job per line: NAME<TAB>command; # comments)
set -u
cd /opt/llm/type1/bench || exit 1
export PY=/opt/llm/type1/.venv/bin/python HF_HOME=/opt/llm/type1/hf TOKENIZERS_PARALLELISM=false
LOG=/opt/llm/type1/logs
queue=$1
watchdog() {   # $1 = pid of the job
  local hot=0
  while kill -0 "$1" 2>/dev/null; do
    t=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | sort -n | tail -1)
    if [ "$t" -ge 88 ]; then hot=$((hot + 5)); else hot=0; fi
    if [ "$hot" -ge 60 ]; then
      echo "$(date -u +%T) WATCHDOG: GPU ${t}C for 60 s, killing job $1" | tee -a "$LOG/queue.log"
      pkill -TERM -P "$1"; kill -TERM "$1"; logger -t type1-queue "GPU overheat, job killed"
      return
    fi
    sleep 5
  done
}
while IFS=$'\t' read -r name cmd; do
  [[ -z "$name" || "$name" == \#* ]] && continue
  if [ -f "/opt/llm/type1/results/$name.json" ]; then echo "$(date -u +%T) skip $name (done)" >> "$LOG/queue.log"; continue; fi
  echo "$(date -u +%T) start $name: $cmd" >> "$LOG/queue.log"
  bash -c "$cmd" > "$LOG/$name.log" 2>&1 &
  pid=$!
  watchdog "$pid" &
  wait "$pid"; rc=$?
  echo "$(date -u +%T) end $name rc=$rc" >> "$LOG/queue.log"
done < "$queue"
echo "$(date -u +%T) QUEUE DONE" >> "$LOG/queue.log"
