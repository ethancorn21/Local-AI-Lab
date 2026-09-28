#!/bin/bash
# A/B watch: session starts per arm, exceptions, arm/test end. One stdout line = one event.
t0=$(date +%s); seen=0
while [ $(( $(date +%s) - t0 )) -lt 1740 ]; do
  out=$(ssh -n -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 harnessvm 'sudo cat /home/agent/ab/run.log 2>/dev/null; for a in A B; do sudo sed "s/^/[$a] /" /home/agent/ab/$a/hollowdeep/.agent/loop.log 2>/dev/null; done; sudo test -f /home/agent/ab/DONE && echo "AB_DONE"; true' 2>/dev/null) || { sleep 60; continue; }
  lines=$(grep -E '^== |^\[[AB]\] .*(iteration [0-9]+:|verified done|REJECTED|STALLED|STUCK|queue empty|unreachable|timeout|hand-over guard|malformed|WARNING: acceptance)|AB_DONE' <<<"$out" | sed -E 's/^(\[[AB]\]) [0-9-]+ /\1 /')
  n=$(grep -c . <<<"$lines")
  [ "$n" -gt "$seen" ] && { tail -n +$((seen + 1)) <<<"$lines" | cut -c1-150; seen=$n; }
  grep -q AB_DONE <<<"$out" && exit 0
  sleep 60
done
