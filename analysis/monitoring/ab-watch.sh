#!/bin/bash
# A/B watch: session starts per arm, exceptions, arm/test end. One stdout line = one event.
t0=$(date +%s); seen=-1   # -1: the first poll only counts what is already there (re-arming does not replay it)
while [ $(( $(date +%s) - t0 )) -lt 1740 ]; do
  out=$(ssh -n -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 harnessvm 'sudo cat /home/agent/ab/run.log /home/agent/ab/queue.log 2>/dev/null; for a in A B O A2 B2; do sudo sed "s/^/[$a] /" /home/agent/ab/$a/hollowdeep/.agent/loop.log 2>/dev/null; done; sudo test -f /home/agent/ab/QUEUE_DONE && echo "QUEUE_DONE"; true' 2>"${TMPDIR:-/tmp}/ab-watch-ssh.err"); rc=$?
  [ $rc -ne 0 ] && { echo "$(date +%T) poll failed rc=$rc: $(tail -1 "${TMPDIR:-/tmp}/ab-watch-ssh.err")" >&2; sleep 60; continue; }
  lines=$(grep -E '^== |^\[(A|B|O|A2|B2)\] .*(iteration [0-9]+:|verified done|REJECTED|STALLED|STUCK|queue empty|unreachable|timeout|hand-over guard|malformed|WARNING: acceptance|WARNING: session)|QUEUE_DONE' <<<"$out" | sed -E 's/^(\[(A|B|O|A2|B2)\]) [0-9-]+ /\1 /')
  n=$(grep -c . <<<"$lines")
  echo "$(date +%T) poll ok: $n lines (seen $seen)" >&2
  if [ "$seen" -lt 0 ]; then seen=$n
  elif [ "$n" -gt "$seen" ]; then tail -n +$((seen + 1)) <<<"$lines" | cut -c1-150; seen=$n; fi
  grep -q QUEUE_DONE <<<"$out" && exit 0
  sleep 60
done
