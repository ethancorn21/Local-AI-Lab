#!/bin/bash
# A/B watch: session starts per arm, exceptions, arm/test end. One stdout line = one event.
t0=$(date +%s); seen=-1   # -1: the first poll only counts what is already there (re-arming does not replay it)
while [ $(( $(date +%s) - t0 )) -lt 1740 ]; do
  out=$(ssh -n -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 harnessvm 'sudo cat /home/agent/ab/run.log 2>/dev/null; for a in A B; do sudo sed "s/^/[$a] /" /home/agent/ab/$a/hollowdeep/.agent/loop.log 2>/dev/null; done; sudo test -f /home/agent/ab/DONE && echo "AB_DONE"; true' 2>/dev/null) || { sleep 60; continue; }
  lines=$(grep -E '^== |^\[[AB]\] .*(iteration [0-9]+:|verified done|REJECTED|STALLED|STUCK|queue empty|unreachable|timeout|hand-over guard|malformed|WARNING: acceptance|WARNING: session)|AB_DONE' <<<"$out" | sed -E 's/^(\[[AB]\]) [0-9-]+ /\1 /')
  n=$(grep -c . <<<"$lines")
  if [ "$seen" -lt 0 ]; then seen=$n
  elif [ "$n" -gt "$seen" ]; then tail -n +$((seen + 1)) <<<"$lines" | cut -c1-150; seen=$n; fi
  grep -q AB_DONE <<<"$out" && exit 0
  sleep 60
done
